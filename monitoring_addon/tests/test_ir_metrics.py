"""Unit tests for monitoring/ir_metrics.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.ir_metrics import (
    average_precision,
    compute_extended_metrics,
    f1_at_k,
    hit_rate,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_hit_rate_basic():
    assert hit_rate(["a", "b"], ["b"]) == 1.0
    assert hit_rate(["x"], ["b"]) == 0.0
    assert hit_rate([], ["b"]) == 0.0
    assert hit_rate(["a"], []) == 0.0


def test_precision_at_k():
    assert precision_at_k(["a", "b", "c"], ["a"], k=3) == 1 / 3
    assert precision_at_k(["a", "b"], ["a", "b"], k=2) == 1.0
    assert precision_at_k(["c"], ["a"], k=5) == 0.0


def test_recall_at_k_distinct_from_hit_rate():
    # Recall@K reports the FRACTION of expected docs recovered,
    # while hit_rate is 1.0 the moment ANY is found.
    retrieved = ["a", "b", "c"]
    relevant = ["a", "d"]  # 2 expected, 1 recovered
    assert hit_rate(retrieved, relevant) == 1.0
    assert recall_at_k(retrieved, relevant, k=3) == 0.5


def test_recall_at_k_k_limit():
    # Only the first K count toward recall.
    retrieved = ["x", "x", "a"]
    relevant = ["a"]
    assert recall_at_k(retrieved, relevant, k=2) == 0.0
    assert recall_at_k(retrieved, relevant, k=3) == 1.0


def test_f1_at_k_harmonic():
    # P = 0.5, R = 0.5 → F1 = 0.5
    retrieved = ["a", "b"]
    relevant = ["a", "c"]
    p = precision_at_k(retrieved, relevant, k=2)
    r = recall_at_k(retrieved, relevant, k=2)
    f1 = f1_at_k(retrieved, relevant, k=2)
    assert abs(p - 0.5) < 1e-9
    assert abs(r - 0.5) < 1e-9
    assert abs(f1 - 0.5) < 1e-9


def test_f1_zero_when_either_zero():
    assert f1_at_k(["x"], ["a"], k=5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], ["b"]) == 0.5
    assert reciprocal_rank(["a", "b", "c"], ["c"]) == 1 / 3
    assert reciprocal_rank(["a"], ["b"]) == 0.0


def test_average_precision():
    # Two distinct relevant docs at positions 1 and 3 of [a, x, b]:
    #   AP = (1/1 + 2/3) / 2 = 0.8333
    ap = average_precision(["a", "x", "b"], ["a", "b"])
    assert abs(ap - (1.0 + 2 / 3) / 2) < 1e-9


def test_average_precision_caps_at_one_for_duplicates():
    # Same relevant doc appearing twice in retrieved counts ONCE.
    ap = average_precision(["r", "r", "r"], ["r"])
    assert ap == 1.0


def test_compute_extended_metrics_skip_empty_ground_truth():
    results = [
        {"retrieved_docs": ["a"], "expected_docs": ["a"]},
        {"retrieved_docs": ["x"], "expected_docs": []},  # skipped
    ]
    m = compute_extended_metrics(results)
    assert m.evaluated == 1
    assert m.skipped == 1
    assert m.hit_rate == 1.0
    assert m.recall_at_k == 1.0


def test_compute_extended_metrics_empty_dataset():
    m = compute_extended_metrics([])
    assert m.evaluated == 0
    assert m.skipped == 0
    assert m.hit_rate == 0.0
