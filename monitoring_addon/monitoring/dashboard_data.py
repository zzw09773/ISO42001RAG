"""
Dashboard Payload Builder

Aggregates everything the dashboard (static or live) needs into a single
JSON-serialisable dict:

  - kpi          : top-line counters (queries, rejections, alerts, health severity)
  - daily_series : last N days of per-day stats (for line charts)
  - vv           : latest V&V report snapshot
  - health       : service-health report snapshot (severity + components)
  - status_bins  : status-page style hourly latency blocks
  - anomalies    : aggregated anomaly_flags from audit logs
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .audit_loader import (
    default_audit_dir,
    filter_by_window,
    list_audit_files,
    load_events,
)
from .baseline_loader import (
    default_golden_path,
    extract_baseline_queries,
    load_golden_dataset,
    load_ragas_report,
    ragas_report_meta,
    load_vv_report,
)
from .config import BUSINESS_GOAL_HIT_RATE
from .health_monitor import build_health_report
from .availability import load_availability
from .alert_checkers import load_integrity_state


_ARTICLE_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零兩]+)\s*條")
_TPE_TZ = timezone(timedelta(hours=8))


def _per_day_stats(events: List[dict]) -> List[dict]:
    """Group events by date and compute per-day summary."""
    by_day: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        ts = e.get("timestamp", "")
        if not ts:
            continue
        day = ts[:10]  # YYYY-MM-DD
        by_day[day].append(e)

    out: List[dict] = []
    for day in sorted(by_day):
        bucket = by_day[day]
        queries = [e for e in bucket if e.get("event_type") == "query"]
        rejs = [e for e in bucket if e.get("event_type") == "rejection"]
        secs = [e for e in bucket if e.get("event_type") == "security_alert"]
        anomalous = [e for e in queries if e.get("anomaly_flags")]
        latencies = [e["response_time_ms"] for e in queries if isinstance(e.get("response_time_ms"), int)]
        out.append({
            "date": day,
            "queries": len(queries),
            "rejections": len(rejs),
            "security_alerts": len(secs),
            "anomalies": len(anomalous),
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
            "rejection_rate": round(len(rejs) / max(len(queries) + len(rejs), 1), 4),
        })
    return out


def _parse_event_time(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TPE_TZ)
    return dt.astimezone(_TPE_TZ)


def _p95(values: List[int]) -> Optional[int]:
    if not values:
        return None
    xs = sorted(values)
    idx = int((len(xs) - 1) * 0.95)
    return xs[idx]


def _latency_status(p95_ms: Optional[int]) -> str:
    """Non-sticky status for one time bucket.

    Each bucket stands alone, status-page style: when the newest bucket returns
    to normal latency, the current status is normal while older bad buckets stay
    visible as history.
    """
    if p95_ms is None:
        return "no_data"
    if p95_ms <= 40_000:
        return "normal"
    if p95_ms <= 50_000:
        return "watch"
    if p95_ms <= 60_000:
        return "warning"
    return "critical"


def _status_bins(events: List[dict], *, hours: int = 24) -> dict:
    """Build recent hourly latency status blocks for dashboard display."""
    end = datetime.now(_TPE_TZ).replace(minute=0, second=0, microsecond=0)
    buckets = [end - timedelta(hours=i) for i in range(hours - 1, -1, -1)]
    lat_by_bucket: Dict[str, List[int]] = {b.isoformat(): [] for b in buckets}
    start = buckets[0]

    for e in events:
        if e.get("event_type") != "query":
            continue
        lat = e.get("response_time_ms")
        if not isinstance(lat, int):
            continue
        dt = _parse_event_time(e.get("timestamp", ""))
        if dt is None or dt < start or dt > end + timedelta(hours=1):
            continue
        key_dt = dt.replace(minute=0, second=0, microsecond=0)
        key = key_dt.isoformat()
        if key in lat_by_bucket:
            lat_by_bucket[key].append(lat)

    bins = []
    for b in buckets:
        key = b.isoformat()
        vals = lat_by_bucket.get(key, [])
        p95 = _p95(vals)
        bins.append({
            "start": key,
            "label": b.strftime("%m/%d %H:00"),
            "queries": len(vals),
            "p95_latency_ms": p95,
            "status": _latency_status(p95),
        })

    latest_with_data = next((b for b in reversed(bins) if b["status"] != "no_data"), None)
    return {
        "kind": "latency_hourly",
        "hours": hours,
        "bins": bins,
        "latest": latest_with_data or bins[-1],
    }


def _safety_controls_summary(events: List[dict]) -> dict:
    """防護守則觸發統計（對應 RAG/docs/SAFETY_CONTROLS.md 守則 ③④①）。

    純顯示的 ISO A.8/A.9「防線有在運作」證據——不驅動健康燈：
      ③ Input Sanitizer  : security_alert 事件，依 threat_type 分類
      ④ Scope Classify   : rejection 事件（範圍外婉拒），依 reason 分類
      ① Authentication   : auth_failure 事件，依 reason 分類
    """
    threat: Counter = Counter()
    rej_reason: Counter = Counter()
    auth_reason: Counter = Counter()
    n_sec = n_rej = n_auth = 0
    for e in events:
        et = e.get("event_type")
        if et == "security_alert":
            n_sec += 1
            threat[e.get("threat_type") or "unknown"] += 1
        elif et == "rejection":
            n_rej += 1
            rej_reason[e.get("reason") or "out_of_scope"] += 1
        elif et == "auth_failure":
            n_auth += 1
            auth_reason[e.get("reason") or "unknown"] += 1
    return {
        "rule3_input_sanitizer": {"rule": "③ Input Sanitizer", "total": n_sec,
                                  "by_threat_type": dict(threat.most_common())},
        "rule4_scope_reject": {"rule": "④ Scope Classify（範圍外婉拒）", "total": n_rej,
                               "by_reason": dict(rej_reason.most_common())},
        "rule1_auth_failure": {"rule": "① Authentication", "total": n_auth,
                               "by_reason": dict(auth_reason.most_common())},
    }


def _anomaly_summary(events: List[dict]) -> List[dict]:
    c: Counter = Counter()
    for e in events:
        for flag in e.get("anomaly_flags") or []:
            key = flag.split(":")[0]
            c[key] += 1
    return [{"flag": k, "count": v} for k, v in c.most_common(20)]


def _kpi(events: List[dict], drift_severity: str) -> dict:
    n_q = sum(1 for e in events if e.get("event_type") == "query")
    n_r = sum(1 for e in events if e.get("event_type") == "rejection")
    n_s = sum(1 for e in events if e.get("event_type") == "security_alert")
    n_a = sum(1 for e in events if e.get("anomaly_flags"))
    latencies = [
        e["response_time_ms"]
        for e in events
        if e.get("event_type") == "query" and isinstance(e.get("response_time_ms"), int)
    ]
    p95 = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 5 else None
    return {
        "queries": n_q,
        "rejections": n_r,
        "rejection_rate": round(n_r / max(n_q + n_r, 1), 4),
        "security_alerts": n_s,
        "anomalies": n_a,
        "p95_latency_ms": p95,
        "drift_severity": drift_severity,
    }


def build_payload(
    *,
    audit_dir: Optional[Path] = None,
    golden_path: Optional[Path] = None,
    vv_report_path: Optional[Path] = None,
    window_days: int = 30,
) -> dict:
    """Assemble the full dashboard payload from disk."""
    files = list_audit_files(audit_dir)
    files_in_window = filter_by_window(files, window_days)
    events = load_events(files_in_window)

    golden = load_golden_dataset(golden_path)
    baseline_queries = extract_baseline_queries(golden)
    vv_report = load_vv_report(vv_report_path)

    # Faithfulness from the latest RAGAS report (run_ragas_evaluation.py).
    # None on a fresh deploy → dashboard shows "尚未評估".
    ragas = load_ragas_report()
    faithfulness_current = (ragas.get("aggregate") or {}).get("faithfulness")

    # Availability (composite probe) + integrity state from the addon data dir.
    _addon_data = Path(__file__).resolve().parent.parent / "data"
    availability = load_availability(_addon_data / "availability_log.jsonl")
    integrity_status = load_integrity_state(_addon_data)

    health = build_health_report(
        events,
        baseline_vv_report=vv_report,
        window_days=window_days,
        baseline_label=str(golden_path or default_golden_path()),
        faithfulness_current=faithfulness_current,
        availability=availability,
        integrity_status=integrity_status,
    )
    health_dict = health.to_dict()
    # Attach RAGAS report provenance/freshness so the dashboard shows how old the
    # faithfulness snapshot is and which judge produced it (a point-in-time
    # measurement must not look "live"). (P5)
    if isinstance(health_dict.get("faithfulness"), dict):
        health_dict["faithfulness"]["report_meta"] = ragas_report_meta(ragas)

    # ─── Business goal evaluation ─────────────────────────────────────
    # The primary acceptance criterion: Hit Rate ≥ BUSINESS_GOAL_HIT_RATE.
    # Status is "met" / "not_met" / "inconclusive" (no ground truth yet).
    ret_metrics = (vv_report or {}).get("retrieval", {})
    evaluated = ret_metrics.get("evaluated", 0)
    hit_rate_current = ret_metrics.get("hit_rate")
    if not vv_report or hit_rate_current is None:
        bg_status = "inconclusive"
        bg_reason = "No V&V report available — run scripts/run_online_vv.py first."
    elif evaluated == 0:
        bg_status = "inconclusive"
        bg_reason = "Golden dataset has no `expected_docs`; Hit Rate not computable."
    elif hit_rate_current >= BUSINESS_GOAL_HIT_RATE:
        bg_status = "met"
        bg_reason = f"Hit Rate {hit_rate_current} ≥ {BUSINESS_GOAL_HIT_RATE}"
    else:
        bg_status = "not_met"
        bg_reason = f"Hit Rate {hit_rate_current} < {BUSINESS_GOAL_HIT_RATE}"

    business_goal = {
        "metric": "hit_rate",
        "target": BUSINESS_GOAL_HIT_RATE,
        "current": hit_rate_current,
        "status": bg_status,
        "reason": bg_reason,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "audit_dir": str(audit_dir or default_audit_dir()),
        "files_loaded": len(files_in_window),
        "business_goal": business_goal,
        "kpi": _kpi(events, health.severity),
        "daily_series": _per_day_stats(events),
        "status_bins": _status_bins(events),
        "anomalies": _anomaly_summary(events),
        "vv": {
            "available": bool(vv_report),
            "snapshot": vv_report,
        },
        "health": health_dict,
        "availability": availability,
        "integrity": {"status": integrity_status},
        "safety_controls": _safety_controls_summary(events),
    }


def write_payload_json(payload: dict, out_path: Path) -> None:
    """Persist payload to JSON for downstream tools (also good audit evidence)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
