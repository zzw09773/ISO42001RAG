from monitoring.health_monitor import (
    PerfDrift, compute_perf_drift, HealthReport, build_health_report)


def test_compute_perf_drift_rejection_and_latency():
    events = [
        {"event_type": "query", "response_time_ms": 20000, "scope_check": "in_scope", "citation_count": 1},
        {"event_type": "query", "response_time_ms": 30000, "scope_check": "in_scope", "citation_count": 1},
        {"event_type": "rejection", "user_query": "x"},
    ]
    perf = compute_perf_drift(events, {"rejection_rate_baseline": 0.0})
    assert perf.queries_observed == 2
    assert perf.rejection_rate_current > 0


def test_build_health_report_has_no_distribution_fields():
    rep = build_health_report(
        [{"event_type": "query", "response_time_ms": 30000, "scope_check": "in_scope"}],
        baseline_vv_report={}, window_days=7,
        faithfulness_current=0.95,
        availability={"uptime_pct": 100.0, "hard_down": False},
        integrity_status="intact")
    d = rep.to_dict()
    assert "data" not in d and "embedding" not in d
    assert d["availability"]["uptime_pct"] == 100.0
    assert d["last_integrity_status"] == "intact"
    assert rep.severity in {"normal", "watch", "warning", "critical", "insufficient_data"}
