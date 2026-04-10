"""
Retrieval Evaluator — ISO 42001 A.6 V&V

Computes standard IR metrics against a golden dataset:
  - Hit Rate: at least one relevant doc retrieved
  - Precision@K: fraction of top-K results that are relevant
  - MRR: Mean Reciprocal Rank of first relevant result

All functions are pure (no I/O) and fully unit-testable.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List


@dataclass
class RetrievalMetrics:
    """Aggregate retrieval metrics across all queries."""
    hit_rate: float = 0.0
    precision_at_k: float = 0.0
    mrr: float = 0.0
    evaluated: int = 0
    skipped: int = 0  # queries with empty expected_docs (no ground truth)

    def to_dict(self) -> dict:
        return {
            "hit_rate": round(self.hit_rate, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "mrr": round(self.mrr, 4),
            "evaluated": self.evaluated,
            "skipped": self.skipped,
        }

    def passes_threshold(self, min_hit_rate: float = 0.6, min_precision: float = 0.5) -> bool:
        """Return True if metrics meet the minimum V&V thresholds."""
        if self.evaluated == 0:
            return False
        return self.hit_rate >= min_hit_rate and self.precision_at_k >= min_precision


def hit_rate(retrieved: List[str], relevant: List[str]) -> float:
    """1.0 if any retrieved doc matches a relevant doc, else 0.0."""
    if not relevant:
        return 0.0
    retrieved_set = {_norm(r) for r in retrieved}
    return 1.0 if any(_norm(r) in retrieved_set for r in relevant) else 0.0


def precision_at_k(retrieved: List[str], relevant: List[str], k: int = 5) -> float:
    """Fraction of top-K retrieved docs that are relevant."""
    if not relevant or not retrieved:
        return 0.0
    top_k = retrieved[:k]
    relevant_set = {_norm(r) for r in relevant}
    hits = sum(1 for doc in top_k if _norm(doc) in relevant_set)
    return hits / len(top_k)


def reciprocal_rank(retrieved: List[str], relevant: List[str]) -> float:
    """Reciprocal rank of the first relevant result (0.0 if none found)."""
    if not relevant:
        return 0.0
    relevant_set = {_norm(r) for r in relevant}
    for rank, doc in enumerate(retrieved, start=1):
        if _norm(doc) in relevant_set:
            return 1.0 / rank
    return 0.0


def compute_retrieval_metrics(results: List[dict], k: int = 5) -> RetrievalMetrics:
    """
    Compute aggregate metrics over a list of result dicts.

    Each dict must have:
      "retrieved_docs": List[str]  — sources returned by retrieval
      "expected_docs":  List[str]  — ground-truth relevant filenames

    Entries with empty expected_docs are skipped (no ground truth).
    """
    hit_rates: List[float] = []
    prec_k: List[float] = []
    rrs: List[float] = []
    skipped = 0

    for result in results:
        expected = result.get("expected_docs") or []
        retrieved = result.get("retrieved_docs") or []

        if not expected:
            skipped += 1
            continue

        hit_rates.append(hit_rate(retrieved, expected))
        prec_k.append(precision_at_k(retrieved, expected, k=k))
        rrs.append(reciprocal_rank(retrieved, expected))

    n = len(hit_rates)
    if n == 0:
        return RetrievalMetrics(evaluated=0, skipped=skipped)

    return RetrievalMetrics(
        hit_rate=sum(hit_rates) / n,
        precision_at_k=sum(prec_k) / n,
        mrr=sum(rrs) / n,
        evaluated=n,
        skipped=skipped,
    )


def _norm(doc: str) -> str:
    return doc.strip().lower()
