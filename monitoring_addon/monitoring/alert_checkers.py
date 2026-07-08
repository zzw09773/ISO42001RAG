"""
Alert checkers — three discrete check classes, one per check cadence.

Each checker exposes a single `check()` entrypoint that the alerting loop
calls on its own schedule. None of them keep their own loop; the service-side
asyncio scheduler controls cadence.

  AnomalyChecker    — every 5 min   — scan recent audit log for anomaly_flags
  DriftChecker      — every 15 min  — run drift, alert when severity worsens
  IntegrityChecker  — every 60 min  — verify_integrity on the day's audit file

Design notes:
  - Each checker is stateless EXCEPT IntegrityChecker, which caches the last
    line offset per file so it doesn't re-read 100MB every hour.
  - Drift's "worsened" comparison uses a tiny state file (last_severity.txt)
    in the monitoring data dir so a restart doesn't reset the comparison.
  - All checkers emit via a single AlertSink instance the caller provides.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional

# Same package
from .alerting import Alert, AlertSink

logger = logging.getLogger(__name__)

_TPE_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 1. Anomaly checker  — per-query anomaly_flags from audit log
# ---------------------------------------------------------------------------

# Severity per flag prefix (we look at the flag's `name:value` head).
# These are intentionally per-flag so a single event with multiple flags
# generates one alert per flag, each at its own severity.
_FLAG_SEVERITY = {
    "latency_spike":        "warning",
    "rejection_surge":      "warning",
    "security_alert_burst": "critical",   # 已有真實安全事件群發
    "consecutive_retries":  "info",
    "stream_output_redacted": "warning",  # 串流輸出含敏感樣式：已過濾後持久化，但已串給 client，需人工檢視
}


class AnomalyChecker:
    """Scan recent audit-log events and emit alerts for anomaly_flags."""

    def __init__(self, audit_dir: Path, sink: AlertSink, window_minutes: int = 5):
        self.audit_dir = Path(audit_dir)
        self.sink = sink
        self.window = timedelta(minutes=window_minutes)
        # Track which (timestamp, flag) we've already alerted on so a
        # follow-up scan that overlaps the window doesn't double-alert
        self._seen: set = set()
        # Bound the seen-cache so it doesn't grow forever.
        self._seen_cap = 5000

    def check(self) -> int:
        """Process events in the window, emit alerts, return count emitted."""
        cutoff = datetime.now(_TPE_TZ) - self.window
        emitted = 0
        for event in self._iter_recent_events(cutoff):
            flags = event.get("anomaly_flags") or []
            if not flags:
                continue
            ts = event.get("timestamp", "")
            session = event.get("session_id", "?")
            for flag in flags:
                # Dedup key uniquely identifies this flag occurrence
                marker = f"{ts}|{flag}"
                if marker in self._seen:
                    continue
                self._seen.add(marker)
                alert = self._flag_to_alert(flag, event)
                if self.sink.emit(alert):
                    emitted += 1
        # Cap seen-cache (drop oldest insertions)
        if len(self._seen) > self._seen_cap:
            self._seen = set(list(self._seen)[-self._seen_cap // 2:])
        if emitted:
            logger.info(f"AnomalyChecker emitted {emitted} alerts")
        return emitted

    def _flag_to_alert(self, flag: str, event: dict) -> Alert:
        head = flag.split(":", 1)[0]
        severity = _FLAG_SEVERITY.get(head, "info")
        return Alert(
            severity=severity,
            source="anomaly",
            title=head,
            message=f"audit anomaly: {flag}",
            evidence={
                "flag": flag,
                "session_id": event.get("session_id"),
                "client_ip": event.get("client_ip"),
                "user_query": (event.get("user_query") or "")[:120],
                "response_time_ms": event.get("response_time_ms"),
                "retry_count": event.get("retry_count"),
                "timestamp": event.get("timestamp"),
            },
            # Dedup window applies per (source, key) — include flag head
            # AND session so distinct sessions aren't lumped together.
            dedup_key=f"anomaly:{head}:{event.get('session_id', '?')}",
        )

    def _iter_recent_events(self, cutoff: datetime) -> Iterable[dict]:
        """Yield events whose timestamp >= cutoff from today's and yesterday's logs."""
        today = datetime.now(_TPE_TZ).date()
        for d_offset in (0, -1):  # today first, then yesterday (for boundary)
            day = today + timedelta(days=d_offset)
            p = self.audit_dir / f"audit_{day.isoformat()}.jsonl"
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = ev.get("timestamp", "")
                        try:
                            ev_dt = datetime.fromisoformat(ts)
                        except ValueError:
                            continue
                        if ev_dt >= cutoff:
                            yield ev
            except Exception as e:
                logger.error(f"Failed reading {p}: {e}")


# ---------------------------------------------------------------------------
# 2. Health checker — periodic health run, alert on numeric severity escalation
# ---------------------------------------------------------------------------

# Numeric severity → alert severity. Only GRADUAL dimensions drive this; the
# binary critical overrides (availability hard-down, audit-chain break) alert
# via their own loops and must never double-fire here.
_HEALTH_TO_ALERT = {
    "insufficient_data": None,
    "normal":   None,
    "watch":    None,         # don't spam at watch level
    "warning":  "warning",
    "critical": "critical",
}

_SEVERITY_RANK = {"insufficient_data": 0, "normal": 0, "watch": 1, "warning": 2, "critical": 3}


class HealthChecker:
    """Run health detection, persist last numeric severity, alert when worsened."""

    def __init__(
        self,
        sink: AlertSink,
        audit_dir: Path,
        golden_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        window_days: int = 7,
        state_file: Optional[Path] = None,
    ):
        self.sink = sink
        self.audit_dir = Path(audit_dir)
        self.golden_path = Path(golden_path) if golden_path else None
        self.output_dir = Path(output_dir) if output_dir else None
        self.window_days = window_days
        # Persistent state in the addon data dir (output_dir.parent), so a restart
        # still detects "worsened". output_dir is data/reports → parent is data/.
        base = self.output_dir.parent if self.output_dir else Path(".")
        self.state_file = state_file or (base / "alerts_health_state.json")

    def check(self) -> int:
        try:
            report = self._run_health()
        except Exception as e:
            logger.error(f"Health run failed: {e}")
            return 0
        score = report.get("overall_score", 0)
        # Alert on the NUMERIC severity only. report["severity"] may be forced to
        # critical by availability hard-down / audit-chain break; those are owned
        # by the availability & integrity loops, so deriving severity from the
        # numeric overall_score here prevents double-emitting. (spec §3.4)
        from monitoring.thresholds import _score_to_level
        from monitoring.config import DRIFT_CONFIRM_WINDOWS
        current = _score_to_level(score)

        st = self._load_state()
        last_alerted = st.get("last_alerted_severity", "normal")
        pend_sev = st.get("pending_severity")
        pend_cnt = int(st.get("pending_count", 0) or 0)

        cur_rank = _SEVERITY_RANK.get(current, 0)
        alerted_rank = _SEVERITY_RANK.get(last_alerted, 0)
        did_alert = False

        if cur_rank < alerted_rank:
            # Recovered below the alerted level → lower the watermark; clear pending.
            last_alerted = current
            pend_sev, pend_cnt = None, 0
        elif cur_rank > alerted_rank:
            # Candidate escalation — require N CONSECUTIVE windows before alerting.
            pend_cnt = pend_cnt + 1 if pend_sev == current else 1
            pend_sev = current
            if pend_cnt >= DRIFT_CONFIRM_WINDOWS:
                sev = _HEALTH_TO_ALERT.get(current)
                if sev:
                    alert = Alert(
                        severity=sev,
                        source="health",
                        title=f"health degraded → {current}",
                        message=(
                            f"服務風險等級升至 {current}（risk_score={score}），"
                            f"連續 {pend_cnt} 窗確認（門檻 {DRIFT_CONFIRM_WINDOWS}）。"
                            f" 觸發理由：{'；'.join(report.get('severity_reasons') or [])}"
                        ),
                        evidence={
                            "last_alerted_severity": last_alerted,
                            "current_severity": current,
                            "overall_score": score,
                            "dimension_scores": report.get("dimension_scores"),
                            "confirm_count": pend_cnt,
                            "confirm_windows_required": DRIFT_CONFIRM_WINDOWS,
                            "severity_reasons": report.get("severity_reasons"),
                            "perf": report.get("perf"),
                            "availability": report.get("availability"),
                        },
                        dedup_key=f"health:{current}",
                    )
                    self.sink.emit(alert)
                    last_alerted = current
                    pend_sev, pend_cnt = None, 0
                    did_alert = True
        else:
            pend_sev, pend_cnt = None, 0

        self._save_state(current, last_alerted, pend_sev, pend_cnt)
        return 1 if did_alert else 0

    def _run_health(self) -> dict:
        """Inline health-detection invocation. Returns the report dict."""
        # Import inside method so a missing dep doesn't break the alerting
        # module at import time.
        from .audit_loader import filter_by_window, list_audit_files, load_events
        from .health_monitor import build_health_report
        from .baseline_loader import load_vv_report, load_ragas_report
        from .availability import load_availability
        from .alert_checkers import load_integrity_state

        files = list_audit_files(self.audit_dir)
        events = load_events(filter_by_window(files, self.window_days))
        vv = load_vv_report()
        # Faithfulness from the latest (schema-gated) RAGAS report so the ALERT
        # path can escalate on a hallucination spike too — not just the dashboard.
        ragas = load_ragas_report()
        faithfulness_current = (ragas.get("aggregate") or {}).get("faithfulness")

        data_dir = self.output_dir.parent if self.output_dir else None
        avail = load_availability(data_dir / "availability_log.jsonl") if data_dir else None
        integ = load_integrity_state(data_dir) if data_dir else "unknown"

        report = build_health_report(
            events,
            baseline_vv_report=vv,
            window_days=self.window_days,
            faithfulness_current=faithfulness_current,
            availability=avail,
            integrity_status=integ,
        )
        return report.to_dict()

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, last_severity: str, last_alerted: str,
                    pending: Optional[str], pending_count: int) -> None:
        try:
            self.state_file.write_text(
                json.dumps({
                    "last_severity": last_severity,          # latest computed (dashboard/trend)
                    "last_alerted_severity": last_alerted,   # moves ONLY on a real alert (fixes #4)
                    "pending_severity": pending,
                    "pending_count": pending_count,
                    "updated_at": datetime.now(_TPE_TZ).isoformat(),
                }),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save drift state: {e}")


# ---------------------------------------------------------------------------
# 3. Integrity checker — verify_integrity on today's audit log
# ---------------------------------------------------------------------------

def load_integrity_state(data_dir) -> str:
    """Read last persisted audit-chain status: 'intact' | 'broken' | 'unknown'.

    Used by build_health_report / dashboard so the C-section integrity card and
    the critical override reflect the latest IntegrityChecker run.
    """
    p = Path(data_dir) / "integrity_state.json"
    if not p.exists():
        return "unknown"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("last_integrity_status", "unknown")
    except Exception:
        return "unknown"


class IntegrityChecker:
    """Run verify_integrity on today's + yesterday's log files; alert if broken."""

    def __init__(self, audit_dir: Path, sink: AlertSink, data_dir: Optional[Path] = None):
        self.audit_dir = Path(audit_dir)
        self.sink = sink
        # addon data dir to persist integrity_state.json (read by HealthReport / dashboard)
        self.data_dir = Path(data_dir) if data_dir else None

    def check(self) -> int:
        # Import the audit logger here so monitoring container doesn't need
        # the whole RAG package at startup — only when this check fires.
        # The verify_integrity is a @staticmethod requiring no RAG state.
        sys.path.insert(0, "/app")  # rag_data is /rag_data but rag code is in rag-api image only
        try:
            from rag_system.core.audit_logger import AuditLogger  # noqa
            verify = AuditLogger.verify_integrity
        except Exception:
            # Fallback: inline minimal verifier so monitoring container works
            # standalone without rag-api code. Matches AuditLogger algorithm.
            verify = _inline_verify_integrity

        emitted = 0
        any_broken = False
        today = datetime.now(_TPE_TZ).date()
        for d_offset in (0, -1):
            day = today + timedelta(days=d_offset)
            p = self.audit_dir / f"audit_{day.isoformat()}.jsonl"
            if not p.exists():
                continue
            result = verify(p)
            if not result.get("valid"):
                any_broken = True   # state 來源，與 emit dedup 解耦
                alert = Alert(
                    severity="critical",
                    source="integrity",
                    title=f"audit chain broken — {day.isoformat()}",
                    message=(
                        f"verify_integrity 回報 audit_{day.isoformat()}.jsonl "
                        f"鏈結損毀於第 {result.get('broken_at')} 行：{result.get('reason')}。"
                        f"可能成因：竄改、非預期寫入、檔案截斷。"
                    ),
                    evidence={
                        "file": str(p),
                        "valid": False,
                        "broken_at": result.get("broken_at"),
                        "reason": result.get("reason"),
                        "total_lines": result.get("total"),
                    },
                    dedup_key=f"integrity:{day.isoformat()}:{result.get('broken_at')}",
                )
                if self.sink.emit(alert):
                    emitted += 1
        if emitted:
            logger.warning(f"IntegrityChecker emitted {emitted} chain-break alerts")
        if self.data_dir:
            status = "broken" if any_broken else "intact"   # 由 verify 結果判定，非 emit 成功與否
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                (self.data_dir / "integrity_state.json").write_text(
                    json.dumps({"last_integrity_status": status,
                                "checked_at": datetime.now(_TPE_TZ).isoformat()}),
                    encoding="utf-8")
            except Exception as e:
                logger.error(f"write integrity_state failed: {e}")
        return emitted


def _inline_verify_integrity(log_file: Path) -> dict:
    """Standalone re-implementation matching AuditLogger.verify_integrity.

    Used when the rag_system package isn't importable in the monitoring
    container — keeps integrity checks operational regardless of layout.
    """
    import hashlib
    GENESIS = "0" * 64
    prev = GENESIS
    total = 0
    try:
        with open(log_file, encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                rec = json.loads(line)
                stored = rec.pop("entry_hash", None)
                if rec.get("prev_hash") != prev:
                    return {"valid": False, "total": total, "broken_at": i,
                            "reason": f"prev_hash mismatch at line {i}"}
                canon = json.dumps(rec, ensure_ascii=False, sort_keys=True)
                recomputed = hashlib.sha256((prev + canon).encode("utf-8")).hexdigest()
                if recomputed != stored:
                    return {"valid": False, "total": total, "broken_at": i,
                            "reason": f"entry_hash mismatch at line {i}"}
                prev = stored
    except FileNotFoundError:
        return {"valid": False, "total": 0, "broken_at": None, "reason": "file not found"}
    return {"valid": True, "total": total, "broken_at": None, "reason": "chain intact"}


__all__ = ["AnomalyChecker", "HealthChecker", "IntegrityChecker", "load_integrity_state"]
