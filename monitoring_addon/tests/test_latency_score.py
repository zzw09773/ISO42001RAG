from monitoring.thresholds import _latency_score


def test_latency_none_returns_none():
    assert _latency_score(None) is None


def test_latency_normal_below_40s():
    assert _latency_score(30_000) < 25      # 30s 觀測基線 → normal


def test_latency_watch_boundary_at_40s():
    assert _latency_score(40_000) == 25     # 40s → watch 邊界


def test_latency_critical_boundary_at_60s():
    assert _latency_score(60_000) == 75     # 60s → critical 邊界


def test_latency_caps_at_100():
    assert _latency_score(200_000) == 100
