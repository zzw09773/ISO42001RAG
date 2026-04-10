"""
Anomaly Detector Unit Tests — ISO 42001 A.6

Tests all anomaly detection scenarios without reading real log files.
"""
import json
import tempfile
from pathlib import Path

import pytest
from rag_system.core.anomaly_detector import AnomalyDetector, analyse_log_file


def _query_event(latency_ms: int = 500, scope: str = "in_scope", retry: int = 0) -> dict:
    return {
        "event_type": "query",
        "response_time_ms": latency_ms,
        "scope_check": scope,
        "retry_count": retry,
    }


def _security_event() -> dict:
    return {"event_type": "security_alert", "threat_type": "prompt_injection"}


def _rejection_event() -> dict:
    return {"event_type": "rejection", "reason": "out_of_scope"}


class TestLatencySpike:

    def test_no_anomaly_when_window_too_small(self):
        d = AnomalyDetector(window=50)
        # Feed only 4 events — threshold requires ≥ 5
        for _ in range(4):
            d.check(_query_event(latency_ms=100))
        flags = d.check(_query_event(latency_ms=9999))
        assert not any("latency_spike" in f for f in flags)

    def test_spike_detected_after_warm_up(self):
        d = AnomalyDetector(window=50)
        # Establish a baseline of 100ms
        for _ in range(10):
            d.check(_query_event(latency_ms=100))
        # Send 1200ms — > 2× p95 (≈100ms)
        flags = d.check(_query_event(latency_ms=1200))
        assert any("latency_spike" in f for f in flags)

    def test_no_spike_within_threshold(self):
        d = AnomalyDetector(window=50)
        for _ in range(10):
            d.check(_query_event(latency_ms=200))
        # 350ms ≈ 1.75× p95, below 2×
        flags = d.check(_query_event(latency_ms=350))
        assert not any("latency_spike" in f for f in flags)


class TestRejectionRateSurge:

    def test_surge_detected_over_50_percent(self):
        d = AnomalyDetector(window=50)
        # 6 rejections out of 10
        for _ in range(4):
            d.check(_query_event(scope="in_scope"))
        for _ in range(6):
            flags = d.check(_query_event(scope="out_of_scope"))
        assert any("rejection_surge" in f for f in flags)

    def test_no_surge_below_threshold(self):
        d = AnomalyDetector(window=50)
        for _ in range(7):
            d.check(_query_event(scope="in_scope"))
        for _ in range(3):
            flags = d.check(_query_event(scope="out_of_scope"))
        assert not any("rejection_surge" in f for f in flags)


class TestConsecutiveRetries:

    def test_retry_2_flagged(self):
        d = AnomalyDetector()
        flags = d.check(_query_event(retry=2))
        assert any("consecutive_retries" in f for f in flags)

    def test_retry_1_not_flagged(self):
        d = AnomalyDetector()
        flags = d.check(_query_event(retry=1))
        assert not any("consecutive_retries" in f for f in flags)


class TestSecurityAlertBurst:

    def test_burst_after_3_alerts(self):
        d = AnomalyDetector()
        d.check(_security_event())
        d.check(_security_event())
        flags = d.check(_security_event())
        assert "security_alert_burst" in flags

    def test_single_alert_not_flagged(self):
        d = AnomalyDetector()
        flags = d.check(_security_event())
        assert "security_alert_burst" not in flags


class TestRejectionEventSurge:
    """Explicit rejection audit events must trigger surge detection."""

    def test_rejection_events_trigger_surge(self):
        d = AnomalyDetector(window=50)
        # 4 queries then 7 rejections — rejections dominate (7/11 ≈ 64%)
        for _ in range(4):
            d.check(_query_event(scope="in_scope"))
        flags = []
        for _ in range(7):
            flags = d.check(_rejection_event())
        assert any("rejection_surge" in f for f in flags)

    def test_few_rejections_not_flagged(self):
        d = AnomalyDetector(window=50)
        # 8 queries + 2 rejections → 20%, under threshold
        for _ in range(8):
            d.check(_query_event(scope="in_scope"))
        flags = []
        for _ in range(2):
            flags = d.check(_rejection_event())
        assert not any("rejection_surge" in f for f in flags)

    def test_rejection_event_does_not_affect_latency_or_security(self):
        d = AnomalyDetector(window=50)
        flags = d.check(_rejection_event())
        assert not any("latency_spike" in f or "security_alert_burst" in f for f in flags)


class TestAnalyseLogFile:

    def _write_log(self, events: list) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        for ev in events:
            tmp.write(json.dumps(ev) + "\n")
        tmp.close()
        return Path(tmp.name)

    def test_basic_counts(self):
        events = [
            {"event_type": "query", "response_time_ms": 100, "scope_check": "in_scope", "retry_count": 0},
            {"event_type": "query", "response_time_ms": 200, "scope_check": "in_scope", "retry_count": 0},
            {"event_type": "rejection", "scope_check": "out_of_scope"},
            {"event_type": "security_alert", "threat_type": "prompt_injection"},
        ]
        path = self._write_log(events)
        result = analyse_log_file(path)

        assert result["total_events"] == 4
        assert result["query_count"] == 2
        assert result["rejection_count"] == 1
        assert result["security_alert_count"] == 1
        assert result["avg_latency_ms"] == 150

    def test_missing_file_returns_error(self):
        result = analyse_log_file(Path("/nonexistent/audit.jsonl"))
        assert "error" in result

    def test_empty_file_returns_zeros(self):
        path = self._write_log([])
        result = analyse_log_file(path)
        assert result["total_events"] == 0
        assert result["query_count"] == 0
