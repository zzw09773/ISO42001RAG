"""
Anomaly Detector — ISO 42001 A.6 Lifecycle Monitoring

Detects statistical anomalies from a sliding window of recent audit events:
  - Response latency spike (> 2× recent p95)
  - Rejection rate surge (> 50% in last N queries)
  - Security alert burst (multiple in short window)
  - Consecutive retries in same session (retry_count ≥ 2)

Usage:
    detector = AnomalyDetector(window=50)
    flags = detector.check(event_dict)   # returns List[str]
"""
from __future__ import annotations

import json
import statistics
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AnomalyDetector:
    """
    Stateful sliding-window anomaly detector.

    Maintains an in-memory deque of recent events and computes thresholds
    dynamically from the window. Designed to be called once per audit event.
    """
    window: int = 50

    # Internal state — not part of public API
    _latencies: deque = field(default_factory=lambda: deque(maxlen=50), init=False, repr=False)
    _scopes: deque = field(default_factory=lambda: deque(maxlen=50), init=False, repr=False)
    _security_ts: deque = field(default_factory=lambda: deque(maxlen=20), init=False, repr=False)
    _rejection_events: deque = field(default_factory=lambda: deque(maxlen=50), init=False, repr=False)

    def __post_init__(self):
        self._latencies = deque(maxlen=self.window)
        self._scopes = deque(maxlen=self.window)
        self._security_ts = deque(maxlen=20)
        self._rejection_events = deque(maxlen=self.window)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, event: Dict[str, Any]) -> List[str]:
        """
        Analyse a single audit event and return a list of anomaly flags.
        Updates internal state as a side effect.
        """
        flags: List[str] = []
        event_type = event.get("event_type", "")

        if event_type == "query":
            flags.extend(self._check_query(event))
        elif event_type == "security_alert":
            flags.extend(self._check_security_burst(event))
        elif event_type == "rejection":
            flags.extend(self._check_rejection_event(event))

        return flags

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_query(self, event: Dict[str, Any]) -> List[str]:
        flags: List[str] = []

        latency = event.get("response_time_ms")
        scope = event.get("scope_check", "")
        retry = event.get("retry_count", 0)

        # 1. Latency spike
        if latency is not None:
            if self._latencies and len(self._latencies) >= 5:
                sorted_lats = sorted(self._latencies)
                p95_idx = int(len(sorted_lats) * 0.95)
                p95 = sorted_lats[min(p95_idx, len(sorted_lats) - 1)]
                if p95 > 0 and latency > p95 * 2:
                    flags.append(f"latency_spike:{latency}ms>2×p95({p95}ms)")
            self._latencies.append(latency)

        # 2. Rejection rate surge
        if scope:
            self._scopes.append(scope)
            if len(self._scopes) >= 10:
                recent = list(self._scopes)[-10:]
                rejection_rate = sum(1 for s in recent if s == "out_of_scope") / len(recent)
                if rejection_rate > 0.5:
                    flags.append(f"rejection_surge:{rejection_rate:.0%}_last10")

        # 3. Consecutive retries
        if retry >= 2:
            flags.append(f"consecutive_retries:{retry}")

        return flags

    def _check_rejection_event(self, event: Dict[str, Any]) -> List[str]:
        """
        Track explicit rejection audit events (event_type='rejection') for
        surge detection. Computes rate against recent queries + rejections so
        that a flood of rejections mixed with few queries is caught even when
        query events don't carry scope_check='out_of_scope'.
        """
        self._rejection_events.append(1)
        total = len(self._scopes) + len(self._rejection_events)
        if total >= 10:
            rejection_rate = len(self._rejection_events) / total
            if rejection_rate > 0.5:
                return [f"rejection_surge:{rejection_rate:.0%}_last{total}"]
        return []

    def _check_security_burst(self, event: Dict[str, Any]) -> List[str]:
        """Flag if ≥ 3 security alerts occur within the last 20 events."""
        self._security_ts.append(1)
        if len(self._security_ts) >= 3:
            return ["security_alert_burst"]
        return []


# ---------------------------------------------------------------------------
# Batch analysis — scan historical audit logs
# ---------------------------------------------------------------------------

def analyse_log_file(log_path: Path, window: int = 50) -> Dict[str, Any]:
    """
    Scan an audit log file (.jsonl) and return aggregate anomaly statistics.

    Returns a dict suitable for inclusion in a monitoring report.
    """
    detector = AnomalyDetector(window=window)
    total = 0
    anomalous = 0
    all_flags: List[str] = []
    query_count = 0
    rejection_count = 0
    security_count = 0
    latencies: List[int] = []

    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                total += 1
                event_type = event.get("event_type", "")

                if event_type == "query":
                    query_count += 1
                    lat = event.get("response_time_ms")
                    if lat is not None:
                        latencies.append(lat)
                elif event_type == "rejection":
                    rejection_count += 1
                elif event_type == "security_alert":
                    security_count += 1

                flags = detector.check(event)
                if flags:
                    anomalous += 1
                    all_flags.extend(flags)

    except FileNotFoundError:
        return {"error": f"Log file not found: {log_path}"}

    avg_latency = int(statistics.mean(latencies)) if latencies else None
    p95_latency = None
    if latencies:
        sorted_lats = sorted(latencies)
        p95_latency = sorted_lats[int(len(sorted_lats) * 0.95)]

    return {
        "log_file": str(log_path),
        "total_events": total,
        "query_count": query_count,
        "rejection_count": rejection_count,
        "security_alert_count": security_count,
        "rejection_rate": round(rejection_count / query_count, 3) if query_count else None,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "anomalous_events": anomalous,
        "anomaly_flags_summary": _count_flags(all_flags),
    }


def _count_flags(flags: List[str]) -> Dict[str, int]:
    """Count occurrences of each flag type (ignoring numeric suffixes)."""
    counts: Dict[str, int] = {}
    for flag in flags:
        key = flag.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
    return counts
