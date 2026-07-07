"""
Alerting — ISO 42001 A.9 / A.6 即時告警

Three responsibilities:
  1. `Alert` — the structured alert record (severity / source / message / evidence).
  2. `AlertSink` — write to `alerts.jsonl` (always), plus optionally email via SMTP.
  3. Dedup — same (source, key) within `_DEDUP_WINDOW_SEC` is suppressed so a
     persistent anomaly doesn't generate hundreds of identical mails.

The JSONL sink mirrors the audit-log discipline: append-only, UTC+8 timestamp,
written under `monitoring_addon/data/alerts.jsonl`. Each line is one JSON
object. Downstream consumers (dashboard, IT) read this file directly.

SMTP backend is optional — driven entirely by env vars so we never accidentally
spam during local development. When `ALERT_SMTP_HOST` is empty, the SMTP path
is silently skipped and only the JSONL line is written.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TPE_TZ = timezone(timedelta(hours=8))

# Same anomaly fired twice within this window → second one suppressed.
# Per-source defaults; can override via env (ALERT_DEDUP_WINDOW_SEC).
_DEDUP_WINDOW_SEC = int(os.environ.get("ALERT_DEDUP_WINDOW_SEC", "600"))  # 10 min


# Severity ordering — INFO < WARNING < CRITICAL. Numeric for comparisons.
_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class Alert:
    """One alert event. severity ∈ {info, warning, critical}."""
    severity: str
    source: str               # "anomaly" / "drift" / "integrity" / "system"
    title: str
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""       # filled by sink if empty
    # `dedup_key` defaults to f"{source}:{title}" — override when finer-grained
    # dedup is needed (e.g., latency_spike on a specific session)
    dedup_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_ORDER.get(self.severity, 0)


@dataclass
class SMTPConfig:
    """SMTP relay config, all read from env. host='' disables SMTP entirely."""
    host: str
    port: int
    use_tls: bool
    user: str
    password: str
    sender: str
    recipients: List[str]

    @classmethod
    def from_env(cls) -> "SMTPConfig":
        host = os.environ.get("ALERT_SMTP_HOST", "").strip()
        port = int(os.environ.get("ALERT_SMTP_PORT", "25"))
        use_tls = os.environ.get("ALERT_SMTP_USE_TLS", "false").lower() in ("true", "1", "yes")
        user = os.environ.get("ALERT_SMTP_USER", "").strip()
        password = os.environ.get("ALERT_SMTP_PASS", "").strip()
        sender = os.environ.get("ALERT_SMTP_FROM", user or "monitoring@iso42001.local").strip()
        recipients_raw = os.environ.get("ALERT_RECIPIENTS", "").strip()
        recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
        return cls(
            host=host, port=port, use_tls=use_tls,
            user=user, password=password,
            sender=sender, recipients=recipients,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.recipients)


class AlertSink:
    """Append alerts to alerts.jsonl + optionally email via SMTP."""

    def __init__(
        self,
        alert_dir: Path,
        smtp_config: Optional[SMTPConfig] = None,
        min_email_severity: str = "warning",
    ):
        self.alert_dir = Path(alert_dir)
        self.alert_dir.mkdir(parents=True, exist_ok=True)
        self.smtp = smtp_config or SMTPConfig.from_env()
        self.min_email_severity = min_email_severity
        # Dedup state — keyed by dedup_key, value = last-emitted epoch
        self._dedup: Dict[str, float] = {}
        self._lock = threading.Lock()
        # SSE pub/sub — subscribers are (asyncio.Queue, asyncio.AbstractEventLoop)
        # pairs so we can cross-thread push from sync emit() into async queues
        # via loop.call_soon_threadsafe.
        self._subscribers: List[Tuple["asyncio.Queue", "asyncio.AbstractEventLoop"]] = []
        self._sub_lock = threading.Lock()

    @property
    def alert_file(self) -> Path:
        # Single rolling file (not daily) — alerts are low-volume and we
        # want the dashboard's "recent 24h" panel to read one file. If
        # this grows beyond ~10MB we'll rotate manually.
        return self.alert_dir / "alerts.jsonl"

    def emit(self, alert: Alert) -> bool:
        """Write alert to disk and (optionally) send via SMTP.

        Returns False if the alert was suppressed by dedup.
        """
        import time
        now = time.time()
        key = alert.dedup_key or f"{alert.source}:{alert.title}"
        with self._lock:
            last = self._dedup.get(key)
            if last is not None and (now - last) < _DEDUP_WINDOW_SEC:
                logger.debug(f"Alert deduped: {key} (last {int(now - last)}s ago)")
                return False
            self._dedup[key] = now

        if not alert.timestamp:
            alert.timestamp = datetime.now(_TPE_TZ).isoformat()

        # ---- 1. JSONL append (always) ------------------------------------
        try:
            with open(self.alert_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(alert.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write alert: {e}")

        # ---- 2. SMTP (optional) ------------------------------------------
        if (
            self.smtp.enabled
            and alert.severity_rank >= _SEVERITY_ORDER.get(self.min_email_severity, 1)
        ):
            try:
                self._send_email(alert)
            except Exception as e:
                logger.error(f"Failed to send alert email: {e}")

        # ---- 3. Push to SSE subscribers ----------------------------------
        # Cross-thread safe: each subscriber registered (queue, loop), so we
        # schedule put_nowait on the correct loop. Loop closed / queue full
        # → silently drop that subscriber's update.
        with self._sub_lock:
            subs = list(self._subscribers)
        payload = alert.to_dict()
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, payload)
            except RuntimeError:
                # Loop closed — best-effort unsubscribe so we stop trying
                self.unsubscribe(q)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber queue full, dropping alert")

        return True

    # ── SSE pub/sub --------------------------------------------------------

    def subscribe(self, loop: "asyncio.AbstractEventLoop", maxsize: int = 100) -> "asyncio.Queue":
        """Register a new SSE subscriber. Returns the queue the caller awaits."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._sub_lock:
            self._subscribers.append((q, loop))
        logger.debug(f"SSE subscriber added (total: {len(self._subscribers)})")
        return q

    def unsubscribe(self, q: "asyncio.Queue") -> None:
        """Remove a subscriber (call on connection close)."""
        with self._sub_lock:
            self._subscribers = [(qq, l) for qq, l in self._subscribers if qq is not q]
        logger.debug(f"SSE subscriber removed (total: {len(self._subscribers)})")

    @property
    def subscriber_count(self) -> int:
        with self._sub_lock:
            return len(self._subscribers)

    def _send_email(self, alert: Alert) -> None:
        """Send a single plain-text alert email via the configured SMTP relay."""
        cfg = self.smtp
        msg = EmailMessage()
        msg["Subject"] = f"[ISO42001:{alert.severity.upper()}] {alert.title}"
        msg["From"] = cfg.sender
        msg["To"] = ", ".join(cfg.recipients)
        body_lines = [
            f"來源 source     : {alert.source}",
            f"等級 severity   : {alert.severity}",
            f"時間 timestamp  : {alert.timestamp}",
            "",
            alert.message,
            "",
            "證據 / evidence:",
            json.dumps(alert.evidence, ensure_ascii=False, indent=2),
            "",
            "（本郵件由 ISO42001 監測系統自動產生，請勿直接回覆。）",
        ]
        msg.set_content("\n".join(body_lines))

        with smtplib.SMTP(cfg.host, cfg.port, timeout=15) as s:
            if cfg.use_tls:
                s.starttls()
            if cfg.user and cfg.password:
                s.login(cfg.user, cfg.password)
            s.send_message(msg)
        logger.info(f"Alert email sent: {alert.severity}/{alert.title}")

    def recent(self, hours: int = 24, max_n: int = 200) -> List[Dict[str, Any]]:
        """Read tail of alerts.jsonl within the last `hours`."""
        if not self.alert_file.exists():
            return []
        cutoff = datetime.now(_TPE_TZ) - timedelta(hours=hours)
        out: List[Dict[str, Any]] = []
        try:
            with open(self.alert_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = rec.get("timestamp", "")
                    try:
                        ts_dt = datetime.fromisoformat(ts)
                    except ValueError:
                        continue
                    if ts_dt >= cutoff:
                        out.append(rec)
        except Exception as e:
            logger.error(f"Failed to read alerts: {e}")
        # Return latest-first, capped at max_n
        return list(reversed(out))[:max_n]

    def severity_counts(self, hours: int = 24) -> Dict[str, int]:
        """Return {info: N, warning: N, critical: N} over the window."""
        counts = {"info": 0, "warning": 0, "critical": 0}
        for rec in self.recent(hours=hours, max_n=10_000):
            sev = rec.get("severity", "info")
            counts[sev] = counts.get(sev, 0) + 1
        return counts


__all__ = ["Alert", "SMTPConfig", "AlertSink"]
