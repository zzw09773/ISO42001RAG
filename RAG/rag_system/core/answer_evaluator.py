"""
Answer Evaluator — ISO 42001 A.6 V&V

Assesses LLM answer quality against golden dataset ground truth:
  - keyword_coverage: fraction of expected_keywords found in answer
  - article_citation_match: fraction of expected_articles cited
  - structure_ok: answer contains required sections
  - is_rejection: answer is a scope-rejection message

All functions are pure (no LLM calls) and fully unit-testable.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List

# Required structural sections in a valid answer
_REQUIRED_SECTIONS = ["具體條文", "參考資料", "思考過程"]

# The standard rejection message prefix
_REJECTION_PREFIX = "本系統僅提供法律文件"


@dataclass
class AnswerScore:
    """Quality score for a single answer."""
    keyword_coverage: float = 0.0      # 0–1
    article_citation_match: float = 0.0  # 0–1
    structure_ok: bool = False
    is_rejection: bool = False

    def overall(self) -> float:
        """Weighted composite score (0–1)."""
        if self.is_rejection:
            return 1.0  # Correct rejection counts as perfect
        return (
            self.keyword_coverage * 0.5
            + self.article_citation_match * 0.3
            + (0.2 if self.structure_ok else 0.0)
        )

    def to_dict(self) -> dict:
        return {
            "keyword_coverage": round(self.keyword_coverage, 3),
            "article_citation_match": round(self.article_citation_match, 3),
            "structure_ok": self.structure_ok,
            "is_rejection": self.is_rejection,
            "overall": round(self.overall(), 3),
        }


def score_answer(
    answer: str,
    expected_keywords: List[str],
    expected_articles: List[str],
    is_out_of_scope: bool = False,
) -> AnswerScore:
    """
    Score a single answer against ground truth.

    Args:
        answer: The LLM-generated answer text.
        expected_keywords: Keywords that should appear in the answer.
        expected_articles: Law/article names that should be cited.
        is_out_of_scope: True if the golden entry expects a rejection.
    """
    answer_lower = answer.lower()
    is_rejection = _REJECTION_PREFIX in answer

    # For out-of-scope queries, only check rejection correctness
    if is_out_of_scope:
        return AnswerScore(is_rejection=is_rejection)

    # Keyword coverage
    kw_hits = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
    kw_cov = kw_hits / len(expected_keywords) if expected_keywords else 1.0

    # Article citation match
    art_hits = sum(1 for art in expected_articles if art.lower() in answer_lower)
    art_match = art_hits / len(expected_articles) if expected_articles else 1.0

    # Structure check — at least one required section present
    structure_ok = any(sec in answer for sec in _REQUIRED_SECTIONS)

    return AnswerScore(
        keyword_coverage=kw_cov,
        article_citation_match=art_match,
        structure_ok=structure_ok,
        is_rejection=is_rejection,
    )


def compute_answer_metrics(scored: List[AnswerScore]) -> dict:
    """Aggregate AnswerScore list into summary metrics."""
    if not scored:
        return {"count": 0}

    n = len(scored)
    return {
        "count": n,
        "avg_keyword_coverage": round(sum(s.keyword_coverage for s in scored) / n, 3),
        "avg_article_match": round(sum(s.article_citation_match for s in scored) / n, 3),
        "structure_ok_rate": round(sum(1 for s in scored if s.structure_ok) / n, 3),
        "avg_overall": round(sum(s.overall() for s in scored) / n, 3),
    }
