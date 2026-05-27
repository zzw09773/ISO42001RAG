"""
Dashboard Payload Builder

Aggregates everything the dashboard (static or live) needs into a single
JSON-serialisable dict:

  - kpi          : top-line counters (queries, rejections, alerts, drift severity)
  - daily_series : last N days of per-day stats (for line charts)
  - vv           : latest V&V report snapshot
  - drift        : latest drift report snapshot (severity + components)
  - distributions: article frequency histogram, length histogram
  - anomalies    : aggregated anomaly_flags from audit logs
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
    load_vv_report,
)
from .config import BUSINESS_GOAL_HIT_RATE
from .drift_detector import build_drift_report


_ARTICLE_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零兩]+)\s*條")


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


def _article_distribution(events: List[dict], top_n: int = 15) -> List[dict]:
    c: Counter = Counter()
    for e in events:
        if e.get("event_type") not in {"query", "rejection"}:
            continue
        q = e.get("user_query") or ""
        for m in _ARTICLE_RE.finditer(q):
            c[m.group(0)] += 1
    return [{"label": k, "count": v} for k, v in c.most_common(top_n)]


def _length_histogram(events: List[dict]) -> List[dict]:
    buckets = {"0-20": 0, "21-50": 0, "51-100": 0, "101-200": 0, "200+": 0}
    for e in events:
        if e.get("event_type") not in {"query", "rejection"}:
            continue
        n = len(e.get("user_query") or "")
        if n <= 20:
            buckets["0-20"] += 1
        elif n <= 50:
            buckets["21-50"] += 1
        elif n <= 100:
            buckets["51-100"] += 1
        elif n <= 200:
            buckets["101-200"] += 1
        else:
            buckets["200+"] += 1
    return [{"label": k, "count": v} for k, v in buckets.items()]


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

    # Drift report (no live embedding by default — pass embed_fn=None and let
    # drift_detector decide whether to call embed-proxy via env vars).
    drift = build_drift_report(
        events,
        baseline_vv_report=vv_report,
        baseline_queries=baseline_queries,
        window_days=window_days,
        baseline_label=str(golden_path or default_golden_path()),
    )

    # ─── Business goal evaluation ─────────────────────────────────────
    # The primary acceptance criterion: Hit Rate ≥ BUSINESS_GOAL_HIT_RATE.
    # Status is "met" / "not_met" / "inconclusive" (no ground truth yet).
    ret_metrics = (vv_report or {}).get("retrieval", {})
    evaluated = ret_metrics.get("evaluated", 0)
    hit_rate_current = ret_metrics.get("hit_rate")
    if not vv_report or hit_rate_current is None:
        bg_status = "inconclusive"
        bg_reason = "No V&V report available — run scripts/run_extended_vv.py first."
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
        "kpi": _kpi(events, drift.severity),
        "daily_series": _per_day_stats(events),
        "article_distribution": _article_distribution(events),
        "length_histogram": _length_histogram(events),
        "anomalies": _anomaly_summary(events),
        "vv": {
            "available": bool(vv_report),
            "snapshot": vv_report,
        },
        "drift": drift.to_dict(),
    }


def write_payload_json(payload: dict, out_path: Path) -> None:
    """Persist payload to JSON for downstream tools (also good audit evidence)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
