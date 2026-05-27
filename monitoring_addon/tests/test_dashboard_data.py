"""Unit tests for monitoring/dashboard_data.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.dashboard_data import (
    _article_distribution,
    _length_histogram,
    _per_day_stats,
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


def test_article_distribution_counts_articles():
    events = [
        _q("2026-05-01", "第46條停役"),
        _q("2026-05-01", "第46條再問"),
        _q("2026-05-01", "第47條"),
    ]
    dist = _article_distribution(events)
    labels = {d["label"]: d["count"] for d in dist}
    assert labels["第46條"] == 2
    assert labels["第47條"] == 1


def test_length_histogram_buckets():
    events = [
        _q("2026-05-01", "短"),
        _q("2026-05-01", "中等長度的查詢字串十五個字"),
        _q("2026-05-01", "x" * 250),
    ]
    hist = {h["label"]: h["count"] for h in _length_histogram(events)}
    assert hist["0-20"] >= 1
    assert hist["200+"] == 1
