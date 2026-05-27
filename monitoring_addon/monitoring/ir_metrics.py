"""
IR Metrics — Recall@K, F1@K and full standard IR suite.

Self-contained reimplementation. Does NOT import from rag_system.* to keep
the addon fully decoupled from the frozen main system.

Metrics provided:
  - hit_rate            : 1.0 if any relevant doc retrieved, else 0.0
  - precision_at_k      : |retrieved∩relevant ∩ top-K| / |top-K|
  - recall_at_k         : |retrieved∩relevant ∩ top-K| / |relevant|
  - f1_at_k             : 2 P R / (P + R)
  - reciprocal_rank     : 1 / rank of first relevant doc
  - average_precision   : mean of precision at every relevant doc position
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class ExtendedMetrics:
    """Aggregate retrieval metrics across a dataset."""
    hit_rate: float = 0.0
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    f1_at_k: float = 0.0
    mrr: float = 0.0
    map_score: float = 0.0
    evaluated: int = 0
    skipped: int = 0
    k: int = 5

    def to_dict(self) -> dict:
        return {
            "hit_rate": round(self.hit_rate, 4),
            "precision_at_k": round(self.precision_at_k, 4),
            "recall_at_k": round(self.recall_at_k, 4),
            "f1_at_k": round(self.f1_at_k, 4),
            "mrr": round(self.mrr, 4),
            "map": round(self.map_score, 4),
            "evaluated": self.evaluated,
            "skipped": self.skipped,
            "k": self.k,
        }


def _norm(doc: str) -> str:
    return (doc or "").strip().lower()


def hit_rate(retrieved: List[str], relevant: List[str]) -> float:
    if not relevant:
        return 0.0
    rset = {_norm(r) for r in retrieved}
    return 1.0 if any(_norm(r) in rset for r in relevant) else 0.0


def precision_at_k(retrieved: List[str], relevant: List[str], k: int = 5) -> float:
    if not relevant or not retrieved:
        return 0.0
    top_k = retrieved[:k]
    rset = {_norm(r) for r in relevant}
    hits = sum(1 for d in top_k if _norm(d) in rset)
    return hits / len(top_k)


def recall_at_k(retrieved: List[str], relevant: List[str], k: int = 5) -> float:
    """Recall@K = relevant∩top-K divided by total relevant.

    Distinct from hit_rate which is 1.0 the moment ANY relevant doc is found.
    Recall@K answers "what fraction of expected docs did we recover?"
    """
    if not relevant or not retrieved:
        return 0.0
    top_k_set = {_norm(d) for d in retrieved[:k]}
    hits = sum(1 for d in relevant if _norm(d) in top_k_set)
    return hits / len(relevant)


def f1_at_k(retrieved: List[str], relevant: List[str], k: int = 5) -> float:
    p = precision_at_k(retrieved, relevant, k=k)
    r = recall_at_k(retrieved, relevant, k=k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def reciprocal_rank(retrieved: List[str], relevant: List[str]) -> float:
    if not relevant:
        return 0.0
    rset = {_norm(r) for r in relevant}
    for rank, doc in enumerate(retrieved, start=1):
        if _norm(doc) in rset:
            return 1.0 / rank
    return 0.0


def average_precision(retrieved: List[str], relevant: List[str]) -> float:
    """Mean of precision values at every position a *new* relevant doc appears.

    Duplicates in `retrieved` are scored once, so AP ∈ [0, 1] always holds.
    Denominator is the number of unique relevant docs.
    """
    if not relevant or not retrieved:
        return 0.0
    rset = {_norm(r) for r in relevant}
    seen: set = set()
    hits = 0
    sum_p = 0.0
    for i, doc in enumerate(retrieved, start=1):
        d = _norm(doc)
        if d in rset and d not in seen:
            seen.add(d)
            hits += 1
            sum_p += hits / i
    if hits == 0:
        return 0.0
    return sum_p / len(rset)


def compute_extended_metrics(results: List[dict], k: int = 5) -> ExtendedMetrics:
    """Aggregate metrics over a list of {retrieved_docs, expected_docs} dicts.

    Entries with empty expected_docs are skipped (no ground truth available).
    """
    hits, precs, recs, f1s, rrs, aps = [], [], [], [], [], []
    skipped = 0

    for entry in results:
        expected = entry.get("expected_docs") or []
        retrieved = entry.get("retrieved_docs") or []
        if not expected:
            skipped += 1
            continue
        hits.append(hit_rate(retrieved, expected))
        precs.append(precision_at_k(retrieved, expected, k))
        recs.append(recall_at_k(retrieved, expected, k))
        f1s.append(f1_at_k(retrieved, expected, k))
        rrs.append(reciprocal_rank(retrieved, expected))
        aps.append(average_precision(retrieved, expected))

    n = len(hits)
    if n == 0:
        return ExtendedMetrics(evaluated=0, skipped=skipped, k=k)

    return ExtendedMetrics(
        hit_rate=sum(hits) / n,
        precision_at_k=sum(precs) / n,
        recall_at_k=sum(recs) / n,
        f1_at_k=sum(f1s) / n,
        mrr=sum(rrs) / n,
        map_score=sum(aps) / n,
        evaluated=n,
        skipped=skipped,
        k=k,
    )
