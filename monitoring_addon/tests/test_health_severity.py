from types import SimpleNamespace
from monitoring.thresholds import classify_health


def _report(**kw):
    perf = SimpleNamespace(rejection_rate_delta=kw.get("rej", 0.0),
                           security_alert_rate_current=kw.get("sec", 0.0),
                           p95_latency_current_ms=kw.get("p95"))
    return SimpleNamespace(
        perf=perf,
        queries_in_window=kw.get("n", 1000),
        faithfulness_current=kw.get("faith"),
        availability=kw.get("avail"),
        last_integrity_status=kw.get("integrity", "intact"),
        dimension_scores={}, overall_score=0.0)


def test_all_healthy_is_normal():
    sev, _ = classify_health(_report(p95=30_000, faith=0.95,
                                     avail={"uptime_pct": 100.0, "hard_down": False}))
    assert sev == "normal"


def test_low_faith_escalates():
    sev, _ = classify_health(_report(faith=0.60, p95=30_000))
    assert sev == "critical"


def test_hard_down_forces_critical_even_if_scores_low():
    sev, _ = classify_health(_report(p95=30_000, faith=0.95,
                                     avail={"uptime_pct": 100.0, "hard_down": True}))
    assert sev == "critical"


def test_chain_broken_forces_critical():
    sev, _ = classify_health(_report(p95=30_000, faith=0.95, integrity="broken"))
    assert sev == "critical"


def test_low_sample_skips_perf_dims():
    r = _report(n=5, rej=0.5, p95=90_000, faith=None, avail=None)
    sev, _ = classify_health(r)
    assert "rejection" not in r.dimension_scores
    assert "latency" not in r.dimension_scores
    assert sev == "insufficient_data"


def test_faith_not_gated_by_sample():
    r = _report(n=5, faith=0.60)
    sev, _ = classify_health(r)
    assert "faithfulness" in r.dimension_scores and sev == "critical"


def test_rejection_and_security_no_longer_drive_severity():
    # 使用者行為（離題/資安提問）導致拒答率、安全告警率飆高，不該判系統 critical
    r = _report(n=1000, rej=0.9, sec=0.9, p95=30_000, faith=0.95,
                avail={"uptime_pct": 100.0, "hard_down": False})
    sev, _ = classify_health(r)
    assert sev == "normal"
    assert "rejection" not in r.dimension_scores
    assert "security" not in r.dimension_scores


def test_recent_availability_drives_current_health_not_old_uptime():
    r = _report(n=1000, p95=30_000, faith=0.95,
                avail={"uptime_pct": 30.0, "recent_ok_pct": 100.0, "hard_down": False})
    sev, reasons = classify_health(r)
    assert sev == "normal"
    assert r.dimension_scores["availability"] == 0.0
    assert any("正常波動" in reason for reason in reasons)
