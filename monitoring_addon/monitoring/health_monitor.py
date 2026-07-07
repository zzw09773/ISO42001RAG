"""
Health Monitor — service-health signals against the audit window.

Performance signals aggregated from audit logs (rejection rate / latency /
citation rate / security-alert rate / retry rate), plus faithfulness (RAGAS),
availability (composite probe) and audit-chain integrity status. Severity is
filled by thresholds.classify_health (worst-of numeric + binary overrides).

Self-contained: does NOT import rag_system.*. Distribution / embedding drift
(PSI / JSD / KL) was intentionally removed — see
docs/superpowers/specs/2026-06-26-monitoring-slimdown-design.md.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


# ───────────── Public dataclasses ─────────────


@dataclass
class PerfDrift:
    rejection_rate_baseline: float = 0.0
    rejection_rate_current: float = 0.0
    rejection_rate_delta: float = 0.0
    avg_latency_baseline_ms: Optional[float] = None
    avg_latency_current_ms: Optional[float] = None
    avg_latency_delta_pct: Optional[float] = None
    p95_latency_current_ms: Optional[float] = None
    citation_rate_baseline: float = 0.0
    citation_rate_current: float = 0.0
    citation_rate_delta: float = 0.0
    security_alert_rate_current: float = 0.0
    retry_rate_current: float = 0.0
    queries_observed: int = 0


@dataclass
class HealthReport:
    generated_at: str = ""
    baseline_label: str = ""
    window_days: int = 0
    queries_in_window: int = 0
    perf: PerfDrift = field(default_factory=PerfDrift)
    # Faithfulness / hallucination (operator's top concern)
    faithfulness_current: Optional[float] = None
    faithfulness_target: float = 0.90
    # Composite-probe availability snapshot (uptime_pct, hard_down, ...) or None
    availability: Optional[dict] = None
    # Latest audit-chain status: "intact" | "broken" | "unknown"
    last_integrity_status: str = "unknown"
    # Filled by thresholds.classify_health
    severity: str = "normal"
    severity_reasons: List[str] = field(default_factory=list)
    overall_score: float = 0.0
    dimension_scores: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "baseline_label": self.baseline_label,
            "window_days": self.window_days,
            "queries_in_window": self.queries_in_window,
            "severity": self.severity,
            "overall_score": self.overall_score,
            "dimension_scores": self.dimension_scores,
            "severity_reasons": self.severity_reasons,
            "perf": self.perf.__dict__,
            "availability": self.availability,
            "last_integrity_status": self.last_integrity_status,
            "faithfulness": {
                "current": self.faithfulness_current,
                "target": self.faithfulness_target,
            },
        }


# ───────────── Performance signals ─────────────


def compute_perf_drift(audit_events: List[dict], baseline: dict) -> PerfDrift:
    query_events = [e for e in audit_events if e.get("event_type") == "query"]
    rejection_events = [e for e in audit_events if e.get("event_type") == "rejection"]
    security_events = [e for e in audit_events if e.get("event_type") == "security_alert"]
    total_user = len(query_events) + len(rejection_events)

    ans_baseline = (baseline or {}).get("answer_quality_baseline", {})
    citation_baseline = float(ans_baseline.get("avg_article_match", 0.0) or 0.0)
    rejection_baseline = float((baseline or {}).get("rejection_rate_baseline", 0.05))

    rejection_current = (
        len(rejection_events) / total_user if total_user else 0.0
    )

    latencies = [
        e["response_time_ms"]
        for e in query_events
        if isinstance(e.get("response_time_ms"), int)
    ]
    avg_lat = statistics.mean(latencies) if latencies else None
    p95_lat = (
        sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 5 else None
    )
    avg_lat_base = (baseline or {}).get("avg_latency_ms_baseline")
    if isinstance(avg_lat_base, (int, float)) and avg_lat:
        lat_delta_pct = (avg_lat - avg_lat_base) / max(avg_lat_base, 1.0)
    else:
        lat_delta_pct = None

    in_scope = [e for e in query_events if e.get("scope_check") == "in_scope"]
    if in_scope:
        with_cite = sum(1 for e in in_scope if (e.get("citation_count") or 0) > 0)
        citation_current = with_cite / len(in_scope)
    else:
        citation_current = 0.0

    retries = sum(1 for e in query_events if (e.get("retry_count") or 0) > 0)
    retry_rate = retries / len(query_events) if query_events else 0.0

    sec_rate = len(security_events) / total_user if total_user else 0.0

    return PerfDrift(
        rejection_rate_baseline=round(rejection_baseline, 4),
        rejection_rate_current=round(rejection_current, 4),
        rejection_rate_delta=round(rejection_current - rejection_baseline, 4),
        avg_latency_baseline_ms=avg_lat_base,
        avg_latency_current_ms=round(avg_lat, 1) if avg_lat else None,
        avg_latency_delta_pct=round(lat_delta_pct, 3) if lat_delta_pct is not None else None,
        p95_latency_current_ms=p95_lat,
        citation_rate_baseline=round(citation_baseline, 4),
        citation_rate_current=round(citation_current, 4),
        citation_rate_delta=round(citation_current - citation_baseline, 4),
        security_alert_rate_current=round(sec_rate, 4),
        retry_rate_current=round(retry_rate, 4),
        queries_observed=len(query_events),
    )


# ───────────── Top-level entry ─────────────


def build_health_report(
    audit_events: List[dict],
    baseline_vv_report: dict,
    *,
    window_days: int = 7,
    baseline_label: str = "vv_baseline",
    faithfulness_current: Optional[float] = None,
    availability: Optional[dict] = None,
    integrity_status: str = "unknown",
) -> HealthReport:
    """Compute perf signals and assemble the health report.

    faithfulness_current: latest RAGAS faithfulness (0–1) or None.
    availability: load_availability(...) snapshot or None.
    integrity_status: load_integrity_state(...) result.
    Severity is filled by thresholds.classify_health.
    """
    from .thresholds import classify_health

    perf = compute_perf_drift(audit_events, baseline_vv_report)
    report = HealthReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        baseline_label=baseline_label,
        window_days=window_days,
        queries_in_window=sum(
            1 for e in audit_events if e.get("event_type") in {"query", "rejection"}
        ),
        perf=perf,
        faithfulness_current=faithfulness_current,
        availability=availability,
        last_integrity_status=integrity_status,
    )
    report.severity, report.severity_reasons = classify_health(report)
    return report
