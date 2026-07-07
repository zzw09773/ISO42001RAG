from monitoring.thresholds import _availability_score


def test_avail_none():
    assert _availability_score(None) is None


def test_avail_full_uptime_normal():
    assert _availability_score(100.0) == 0.0


def test_avail_99_is_normal_zone():
    assert _availability_score(99.0) < 25


def test_avail_95_is_critical_boundary():
    assert _availability_score(95.0) == 75


def test_avail_below_95_critical():
    assert _availability_score(90.0) > 75
