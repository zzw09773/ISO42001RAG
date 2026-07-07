"""Unit tests for monitoring/attribution.py (per-step failure attribution)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.attribution import (
    attribute_one,
    expected_law_articles,
    law_article_set,
    parse_retrieved_doc,
    summarize,
)


# ── parsing ─────────────────────────────────────────────────────────────

def test_parse_retrieved_doc():
    assert parse_retrieved_doc("陸海空軍懲罰法.md#第14條") == ("陸海空軍懲罰法", "第14條")
    assert parse_retrieved_doc("軍人權益事件處理法.md#第8條") == ("軍人權益事件處理法", "第8條")
    assert parse_retrieved_doc("no-hash") is None


def test_law_article_set_dedup():
    s = law_article_set(["A.md#第1條", "A.md#第1條", "B.md#第2條"])
    assert s == {("A", "第1條"), ("B", "第2條")}


# ── law-aware expected set ──────────────────────────────────────────────

def test_expected_prefers_law_qualified_docs():
    s = expected_law_articles(["懲罰法.md#第4條"], ["第4條"])
    assert s == {("懲罰法", "第4條")}


def test_expected_falls_back_to_article_only():
    s = expected_law_articles([], ["第13條"])
    assert s == {("*", "第13條")}


# ── attribution classification ──────────────────────────────────────────

def test_hit_when_expected_cited():
    a = attribute_one(["懲罰法.md#第13條"], ["第13條"], ["第13條"],
                      ["懲罰法.md#第13條"], is_hit=True)
    assert a["label"] == "hit"


def test_r_miss_expected_not_in_context():
    # eval_m10 shape: expected 第13條, but context only had 第14條/第2條/第76條
    a = attribute_one(
        expected_docs=["陸海空軍懲罰法.md#第13條"],
        expected_articles=["第13條"],
        cited_articles=["第14條", "第2條", "第76條"],
        retrieved_docs=["陸海空軍懲罰法.md#第14條", "陸海空軍懲罰法.md#第2條",
                        "陸海空軍懲罰法.md#第76條"],
        is_hit=False,
    )
    assert a["label"] == "R-miss"
    assert "檢索層" in a["reason"]


def test_g_miss_expected_in_context_not_cited():
    # Expected article WAS retrieved but the answer cited something else
    a = attribute_one(
        expected_docs=["懲罰法.md#第13條"],
        expected_articles=["第13條"],
        cited_articles=["第14條"],
        retrieved_docs=["懲罰法.md#第13條", "懲罰法.md#第14條"],
        is_hit=False,
    )
    assert a["label"] == "G-miss"
    assert "生成層" in a["reason"]


def test_hallucinated_citation_flagged():
    a = attribute_one(
        expected_docs=["懲罰法.md#第13條"],
        expected_articles=["第13條"],
        cited_articles=["第99條"],          # cited, but not in context
        retrieved_docs=["懲罰法.md#第13條"],
        is_hit=False,
    )
    # 第13條 was retrieved but not cited → G-miss; 第99條 cited not retrieved → halluc flag
    assert a["label"] == "G-miss"
    assert a["hallucinated_citation"] is True


def test_no_audit_match_returns_sentinel():
    a = attribute_one(["懲罰法.md#第13條"], ["第13條"], ["第14條"],
                      retrieved_docs=None, is_hit=False)
    assert a["label"] == "no-audit-match"


def test_cross_law_no_false_match():
    # Both laws have 第8條; expected is 權益法第8條, context has 懲罰法第8條 only.
    # Law-aware comparison must NOT treat this as retrieved.
    a = attribute_one(
        expected_docs=["軍人權益事件處理法.md#第8條"],
        expected_articles=["第8條"],
        cited_articles=["第10條"],
        retrieved_docs=["陸海空軍懲罰法.md#第8條"],
        is_hit=False,
    )
    assert a["label"] == "R-miss"  # 權益法第8條 not in context (懲罰法第8條 ≠)


def test_skipped_when_no_ground_truth():
    a = attribute_one([], [], ["第1條"], ["x.md#第1條"], is_hit=False)
    assert a["label"] == "skipped"


def test_summarize_counts():
    atts = [{"label": "hit"}, {"label": "hit"}, {"label": "R-miss"}, {"label": "G-miss"}]
    assert summarize(atts) == {"hit": 2, "R-miss": 1, "G-miss": 1}
