from monitoring.alert_checkers import HealthChecker
from monitoring.config import DRIFT_CONFIRM_WINDOWS


class CollectSink:
    def __init__(self): self.alerts = []
    def emit(self, alert): self.alerts.append(alert); return True


def _checker(tmp_path, report):
    hc = HealthChecker(CollectSink(), tmp_path / "audit", output_dir=tmp_path / "reports")
    hc._run_health = lambda: report          # stub disk-reading run
    return hc


def test_warning_requires_n_consecutive_windows(tmp_path):
    rep = {"severity": "warning", "overall_score": 60,
           "dimension_scores": {"latency": 60}, "severity_reasons": [],
           "perf": {}, "availability": {}}
    hc = _checker(tmp_path, rep)
    for _ in range(DRIFT_CONFIRM_WINDOWS - 1):
        assert hc.check() == 0          # pending, not yet confirmed
    assert hc.check() == 1              # N-th consecutive → alert
    assert hc.sink.alerts[0].severity == "warning"
    assert hc.sink.alerts[0].source == "health"


def test_override_only_critical_does_not_alert(tmp_path):
    # numeric overall_score is 0 → numeric severity normal; the report's critical
    # comes only from hard_down, which the availability loop owns, not HealthChecker.
    rep = {"severity": "critical", "overall_score": 0, "dimension_scores": {},
           "severity_reasons": ["hard-down"], "perf": {},
           "availability": {"hard_down": True}}
    hc = _checker(tmp_path, rep)
    assert hc.check() == 0
    assert hc.check() == 0
    assert hc.sink.alerts == []


def test_no_alert_when_normal(tmp_path):
    rep = {"severity": "normal", "overall_score": 10,
           "dimension_scores": {"latency": 10}, "severity_reasons": [],
           "perf": {}, "availability": {}}
    hc = _checker(tmp_path, rep)
    assert hc.check() == 0
