"""Unit tests for monitoring/regression_gate.py.

Includes a reconstruction of the real 2026-05-27 HyDE failed-set swap
(pre-hyde fails {m03, cr02} → hyde-only fails {m10, cr04}, aggregate Hit
Rate unchanged at 0.9355) — the case this gate exists to catch.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.regression_gate import (
    compare_reports,
    gate_verdict,
    load_stability_records,
    query_status,
    record_flip_observation,
    rerun_verdict,
    save_stability_records,
)


def _q(qid, hit=1.0, category="single_article", rejection=None, expected=None):
    return {
        "id": qid,
        "query": f"query-{qid}",
        "category": category,
        "difficulty": "medium",
        "expected_articles": expected or ["第13條"],
        "cited_articles": ["第13條"] if hit else ["第14條"],
        "is_rejection_correct": rejection,
        "hit_rate": hit if category != "out_of_scope" else None,
    }


def _report(records):
    return {"per_query": records}


# ── query_status ────────────────────────────────────────────────────────

def test_status_in_scope_pass_fail_and_skipped():
    assert query_status(_q("a", hit=1.0)) == "pass"
    assert query_status(_q("b", hit=0.0)) == "fail"
    assert query_status(_q("c", hit=None)) == "skipped"  # no golden articles


def test_status_out_of_scope_uses_rejection():
    assert query_status(_q("o1", category="out_of_scope", rejection=True)) == "pass"
    assert query_status(_q("o2", category="out_of_scope", rejection=False)) == "fail"


# ── compare_reports ─────────────────────────────────────────────────────

def test_hyde_failed_set_swap_is_caught():
    # Real case: aggregate identical, failed set swapped — gate must see it.
    pre_hyde = _report([_q("m03", 0.0), _q("cr02", 0.0), _q("m10", 1.0), _q("cr04", 1.0)])
    hyde = _report([_q("m03", 1.0), _q("cr02", 1.0), _q("m10", 0.0), _q("cr04", 0.0)])
    c = compare_reports(pre_hyde, hyde)
    assert sorted(c["newly_failed"]) == ["cr04", "m10"]
    assert sorted(c["newly_passed"]) == ["cr02", "m03"]
    # And the verdict FAILs despite net-zero aggregate:
    assert gate_verdict(c, None)["verdict"] == "FAIL"


def test_dataset_mismatch_is_surfaced_not_silent():
    base = _report([_q("a"), _q("b")])
    cur = _report([_q("a"), _q("c")])
    c = compare_reports(base, cur)
    assert c["only_in_baseline"] == ["b"]
    assert c["only_in_current"] == ["c"]
    v = gate_verdict(c, None)
    assert v["verdict"] == "PASS"  # no flips among common ids
    assert any("題目集不一致" in r for r in v["reasons"])


# ── rerun_verdict: conservative 2:1 rule ───────────────────────────────

def test_unanimous_pass_clears():
    rv = rerun_verdict(["pass", "pass", "pass"])
    assert rv["cleared"] and rv["unanimous"] and rv["majority"] == "pass"


def test_two_one_split_never_clears():
    rv = rerun_verdict(["pass", "pass", "fail"])
    assert rv["majority"] == "pass"          # majority passes…
    assert not rv["unanimous"]
    assert not rv["cleared"]                 # …but ambiguity does NOT clear


def test_unanimous_fail_confirms_regression():
    rv = rerun_verdict(["fail", "fail", "fail"])
    assert not rv["cleared"] and rv["unanimous"] and rv["majority"] == "fail"


# ── gate_verdict end-to-end ─────────────────────────────────────────────

def _flip_comparison():
    base = _report([_q("m10", 1.0), _q("ok", 1.0)])
    cur = _report([_q("m10", 0.0), _q("ok", 1.0)])
    return compare_reports(base, cur)


def test_no_flips_is_pass():
    same = _report([_q("a"), _q("b")])
    assert gate_verdict(compare_reports(same, same), None)["verdict"] == "PASS"


def test_unverified_flip_fails_conservatively():
    v = gate_verdict(_flip_comparison(), None)
    assert v["verdict"] == "FAIL"
    assert any("未執行重跑" in r for r in v["reasons"])


def test_flip_cleared_by_unanimous_rerun_passes():
    reruns = {"m10": rerun_verdict(["pass", "pass", "pass"])}
    assert gate_verdict(_flip_comparison(), reruns)["verdict"] == "PASS"


def test_flip_with_ambiguous_rerun_fails():
    reruns = {"m10": rerun_verdict(["pass", "fail", "pass"])}
    v = gate_verdict(_flip_comparison(), reruns)
    assert v["verdict"] == "FAIL"
    assert any("2:1 含糊" in r for r in v["reasons"])


# ── stability ledger ────────────────────────────────────────────────────

def test_ledger_marks_chronic_flapper_and_roundtrips(tmp_path):
    ledger = {}
    record_flip_observation(ledger, "m10", date="2026-05-27", tag="hyde",
                            direction="newly_failed",
                            rerun=rerun_verdict(["fail", "fail", "fail"]))
    assert ledger["m10"]["unstable"] is False  # one unanimous observation
    record_flip_observation(ledger, "m10", date="2026-06-11", tag="v1.2fix",
                            direction="newly_passed")
    assert ledger["m10"]["unstable"] is True   # second flip ⇒ chronic flapper

    path = tmp_path / "stability.json"
    save_stability_records(ledger, path)
    assert load_stability_records(path)["m10"]["observations"][1]["kind"] == "flip_unverified"


def test_ledger_nonunanimous_rerun_marks_unstable_immediately():
    ledger = {}
    record_flip_observation(ledger, "cr04", date="2026-06-11", tag="t",
                            direction="newly_failed",
                            rerun=rerun_verdict(["pass", "fail", "fail"]))
    assert ledger["cr04"]["unstable"] is True
