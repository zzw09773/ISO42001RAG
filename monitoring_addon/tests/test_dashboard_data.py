"""Unit tests for monitoring/dashboard_data.py."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.dashboard_data import (
    _per_day_stats,
    _safety_controls_summary,
    _status_bins,
    build_payload,
)


def _q(day, q, lat=500):
    return {
        "event_type": "query",
        "timestamp": f"{day}T10:00:00",
        "user_query": q,
        "scope_check": "in_scope",
        "response_time_ms": lat,
    }


def test_per_day_stats_groups_by_date():
    events = [
        _q("2026-05-01", "第46條"),
        _q("2026-05-01", "第47條"),
        _q("2026-05-02", "第50條"),
    ]
    days = _per_day_stats(events)
    assert len(days) == 2
    assert days[0]["date"] == "2026-05-01"
    assert days[0]["queries"] == 2
    assert days[1]["queries"] == 1


def test_payload_has_health_not_drift_or_distribution(tmp_path):
    # empty audit_dir → no events, but payload structure must be the new shape
    payload = build_payload(audit_dir=tmp_path, window_days=30)
    assert "health" in payload and "drift" not in payload
    assert "article_distribution" not in payload
    assert "length_histogram" not in payload
    assert "availability" in payload and "integrity" in payload
    assert "safety_controls" in payload
    assert "status_bins" in payload


def test_status_bins_recover_when_latest_bucket_is_normal():
    tz = timezone(timedelta(hours=8))
    now_hour = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    previous_hour = now_hour - timedelta(hours=1)
    events = [
        {
            "event_type": "query",
            "timestamp": previous_hour.isoformat(),
            "response_time_ms": 65000,
        },
        {
            "event_type": "query",
            "timestamp": now_hour.isoformat(),
            "response_time_ms": 1200,
        },
    ]

    status = _status_bins(events, hours=3)
    assert status["latest"]["status"] == "normal"
    assert any(b["status"] == "critical" for b in status["bins"])


def test_safety_controls_summary_counts():
    events = [
        {"event_type": "security_alert", "threat_type": "prompt_injection"},
        {"event_type": "security_alert", "threat_type": "prompt_injection"},
        {"event_type": "security_alert", "threat_type": "sql_injection"},
        {"event_type": "rejection", "reason": "out_of_scope"},
        {"event_type": "auth_failure", "reason": "invalid_key"},
        {"event_type": "query"},
    ]
    sc = _safety_controls_summary(events)
    assert sc["rule3_input_sanitizer"]["total"] == 3
    assert sc["rule3_input_sanitizer"]["by_threat_type"]["prompt_injection"] == 2
    assert sc["rule4_scope_reject"]["total"] == 1
    assert sc["rule1_auth_failure"]["total"] == 1
