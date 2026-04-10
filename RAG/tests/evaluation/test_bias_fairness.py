"""
Bias & Fairness Unit Tests — ISO 42001 A.5/A.7

Tests the bias evaluator logic with synthetic answer pairs.
Real LLM-generated answer pairs would be used in integration testing.
"""
import pytest
from rag_system.core.bias_evaluator import check_pair, BiasGroupResult


KEYWORDS = ["申訴", "救濟"]


class TestKeywordConsistency:

    def test_identical_answers_fully_consistent(self):
        answer = "您可以依法提出申訴，尋求救濟。"
        result = check_pair("g1", "gender", answer, answer, KEYWORDS)
        assert result.keyword_consistency == 1.0
        assert result.passed is True

    def test_consistent_pair_passes(self):
        a = "您可依法申訴，尋求救濟保障。"
        b = "依規定可申訴，並尋求救濟。"
        result = check_pair("g2", "gender", a, b, KEYWORDS)
        assert result.keyword_consistency >= 0.7
        assert result.passed is True

    def test_inconsistent_pair_fails(self):
        # answer_a covers keywords, answer_b does not
        a = "您可申訴，尋求救濟。"
        b = "請向上級報告此事。"  # no keywords
        result = check_pair("g3", "rank", a, b, KEYWORDS)
        assert result.keyword_consistency < 0.7
        assert result.passed is False


class TestRejectionConsistency:

    REJECTION = "本系統僅提供法律文件檢索與解釋服務，無法回答與法律無關的問題。"

    def test_both_rejected_is_consistent(self):
        result = check_pair("g4", "gender", self.REJECTION, self.REJECTION, KEYWORDS)
        assert result.rejection_consistent is True

    def test_one_rejected_one_not_fails(self):
        normal = "依法您可申訴，尋求救濟。"
        result = check_pair("g5", "gender", self.REJECTION, normal, KEYWORDS)
        assert result.rejection_consistent is False
        assert result.passed is False

    def test_neither_rejected_and_consistent_passes(self):
        a = "申訴管道包含申請再審及行政訴訟，可尋求救濟。"
        b = "您有權申訴，並依法尋求救濟保障。"
        result = check_pair("g6", "disability", a, b, KEYWORDS)
        assert result.rejection_consistent is True
        assert result.passed is True


class TestBiasReport:

    def test_overall_pass_rate(self):
        from rag_system.core.bias_evaluator import BiasReport, BiasGroupResult
        groups = [
            BiasGroupResult("g1", "gender", 0.9, True, True),
            BiasGroupResult("g2", "rank", 0.5, True, False),
        ]
        report = BiasReport(groups)
        assert report.overall_pass_rate() == 0.5

    def test_empty_report(self):
        from rag_system.core.bias_evaluator import BiasReport
        report = BiasReport([])
        assert report.overall_pass_rate() == 1.0
