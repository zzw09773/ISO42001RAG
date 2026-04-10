"""
V&V Pipeline Unit Tests — ISO 42001 A.6

Tests all metric computation functions without LLM calls.
"""
import pytest
from rag_system.core.retrieval_evaluator import (
    hit_rate, precision_at_k, reciprocal_rank,
    compute_retrieval_metrics, RetrievalMetrics,
)
from rag_system.core.answer_evaluator import score_answer, compute_answer_metrics


# ---------------------------------------------------------------------------
# Retrieval metric tests
# ---------------------------------------------------------------------------

class TestHitRate:
    def test_hit_when_doc_matches(self):
        assert hit_rate(["doc_a.md", "doc_b.md"], ["doc_a.md"]) == 1.0

    def test_no_hit_when_nothing_matches(self):
        assert hit_rate(["doc_c.md"], ["doc_a.md"]) == 0.0

    def test_case_insensitive(self):
        assert hit_rate(["Doc_A.MD"], ["doc_a.md"]) == 1.0

    def test_empty_relevant_returns_zero(self):
        assert hit_rate(["doc_a.md"], []) == 0.0

    def test_empty_retrieved_returns_zero(self):
        assert hit_rate([], ["doc_a.md"]) == 0.0


class TestPrecisionAtK:
    def test_all_relevant(self):
        assert precision_at_k(["a.md", "b.md"], ["a.md", "b.md"], k=2) == 1.0

    def test_half_relevant(self):
        assert precision_at_k(["a.md", "c.md"], ["a.md", "b.md"], k=2) == 0.5

    def test_truncates_at_k(self):
        # Only top 1 checked, first doc is irrelevant
        assert precision_at_k(["c.md", "a.md"], ["a.md"], k=1) == 0.0

    def test_empty_relevant_returns_zero(self):
        assert precision_at_k(["a.md"], [], k=5) == 0.0


class TestReciprocalRank:
    def test_first_position(self):
        assert reciprocal_rank(["a.md", "b.md"], ["a.md"]) == 1.0

    def test_second_position(self):
        assert reciprocal_rank(["c.md", "a.md"], ["a.md"]) == 0.5

    def test_no_relevant_found(self):
        assert reciprocal_rank(["c.md"], ["a.md"]) == 0.0

    def test_empty_relevant(self):
        assert reciprocal_rank(["a.md"], []) == 0.0


class TestComputeRetrievalMetrics:
    def test_all_perfect(self):
        results = [
            {"retrieved_docs": ["a.md"], "expected_docs": ["a.md"]},
            {"retrieved_docs": ["b.md"], "expected_docs": ["b.md"]},
        ]
        m = compute_retrieval_metrics(results)
        assert m.hit_rate == 1.0
        assert m.mrr == 1.0
        assert m.evaluated == 2
        assert m.skipped == 0

    def test_no_ground_truth_skipped(self):
        results = [
            {"retrieved_docs": ["a.md"], "expected_docs": []},
            {"retrieved_docs": ["b.md"], "expected_docs": []},
        ]
        m = compute_retrieval_metrics(results)
        assert m.evaluated == 0
        assert m.skipped == 2

    def test_mixed_hits(self):
        results = [
            {"retrieved_docs": ["a.md"], "expected_docs": ["a.md"]},   # hit
            {"retrieved_docs": ["c.md"], "expected_docs": ["a.md"]},   # miss
        ]
        m = compute_retrieval_metrics(results)
        assert m.hit_rate == 0.5
        assert m.evaluated == 2

    def test_passes_threshold(self):
        m = RetrievalMetrics(hit_rate=0.8, precision_at_k=0.7, mrr=0.6, evaluated=10)
        assert m.passes_threshold(min_hit_rate=0.6, min_precision=0.5)

    def test_fails_threshold(self):
        m = RetrievalMetrics(hit_rate=0.3, precision_at_k=0.2, mrr=0.1, evaluated=10)
        assert not m.passes_threshold(min_hit_rate=0.6, min_precision=0.5)


class TestVvStatus:
    """passes_thresholds / vv_status must not issue PASS when evaluated == 0."""

    def test_inconclusive_when_no_ground_truth(self):
        from scripts.run_vv_evaluation import vv_status
        m = RetrievalMetrics(hit_rate=0.0, precision_at_k=0.0, mrr=0.0, evaluated=0, skipped=30)
        assert vv_status(m) == "inconclusive"

    def test_passes_thresholds_false_when_inconclusive(self):
        from scripts.run_vv_evaluation import passes_thresholds
        m = RetrievalMetrics(hit_rate=0.0, precision_at_k=0.0, mrr=0.0, evaluated=0, skipped=30)
        assert passes_thresholds(m) is False

    def test_pass_when_metrics_meet_thresholds(self):
        from scripts.run_vv_evaluation import vv_status
        m = RetrievalMetrics(hit_rate=0.9, precision_at_k=0.8, mrr=0.7, evaluated=20)
        assert vv_status(m) == "pass"

    def test_fail_when_metrics_below_thresholds(self):
        from scripts.run_vv_evaluation import vv_status
        m = RetrievalMetrics(hit_rate=0.1, precision_at_k=0.1, mrr=0.1, evaluated=20)
        assert vv_status(m) == "fail"


# ---------------------------------------------------------------------------
# Answer quality tests
# ---------------------------------------------------------------------------

class TestScoreAnswer:
    def test_perfect_in_scope(self):
        s = score_answer(
            answer="依陸海空軍懲罰法，酒駕處以悔過或罰款。\n具體條文：第8條。\n參考資料：陸海空軍懲罰法。",
            expected_keywords=["酒駕", "悔過", "罰款"],
            expected_articles=["陸海空軍懲罰法"],
            is_out_of_scope=False,
        )
        assert s.keyword_coverage == 1.0
        assert s.article_citation_match == 1.0
        assert s.structure_ok is True
        assert s.overall() > 0.9

    def test_rejection_for_out_of_scope(self):
        s = score_answer(
            answer="本系統僅提供法律文件檢索與解釋服務，無法回答與法律無關的問題。",
            expected_keywords=["無法回答"],
            expected_articles=[],
            is_out_of_scope=True,
        )
        assert s.is_rejection is True
        assert s.overall() == 1.0

    def test_missing_keywords_lowers_score(self):
        s = score_answer(
            answer="這是一個與法律相關的回答。",
            expected_keywords=["酒駕", "悔過", "罰款", "懲罰"],
            expected_articles=[],
        )
        assert s.keyword_coverage == 0.0

    def test_partial_keyword_coverage(self):
        s = score_answer(
            answer="酒駕行為受到懲罰，依法處以悔過。",
            expected_keywords=["酒駕", "悔過", "罰款"],
            expected_articles=[],
        )
        assert s.keyword_coverage == pytest.approx(2 / 3, rel=0.01)


class TestComputeAnswerMetrics:
    def test_empty_list(self):
        result = compute_answer_metrics([])
        assert result["count"] == 0

    def test_aggregate(self):
        from rag_system.core.answer_evaluator import AnswerScore
        scores = [
            AnswerScore(keyword_coverage=1.0, article_citation_match=1.0, structure_ok=True),
            AnswerScore(keyword_coverage=0.5, article_citation_match=0.5, structure_ok=False),
        ]
        result = compute_answer_metrics(scores)
        assert result["count"] == 2
        assert result["avg_keyword_coverage"] == 0.75
        assert result["structure_ok_rate"] == 0.5
