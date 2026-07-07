"""Per-step failure attribution — retrieval layer vs generation layer.

Motivation (see docs/ATTRIBUTION.md): a query that fails Hit Rate can fail
for two very different reasons, and the fix lives in different code:

  - R-miss  (retrieval): the expected article never reached the model's
            context → fix the retrieval layer (chunking / rerank / HyDE).
  - G-miss  (generation): the expected article WAS in context but the
            answer didn't cite it → fix the generate prompt / grounding.
  - G-halluc(generation): the answer cited an article that was NOT in
            context → hallucinated citation → fix grounding / verify.

Before this tool, eval_m10 failed for ~8 versions with nobody able to say
which layer was at fault. The attribution joins the online V&V report
(answer-side: cited_articles) with the audit log (retrieval-side:
retrieved_docs, the post-rerank final context) per query.

Matching is law-aware: retrieved_docs carry "<law>.md#第N條", and the
golden set's expected_docs likewise name the law, so the two-law article
overlap (both laws have 第1..78條) does not cause cross-law false matches.

This module is pure logic (unit-tested); I/O lives in
scripts/run_attribution.py.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

HIT = "hit"
R_MISS = "R-miss"
G_MISS = "G-miss"
G_HALLUC = "G-halluc"
SKIPPED = "skipped"
NO_AUDIT = "no-audit-match"

_ARTICLE_RE = re.compile(r"第\s*([0-9〇零一二三四五六七八九十百兩]+)\s*條")


def parse_retrieved_doc(doc: str) -> Optional[Tuple[str, str]]:
    """Parse 'chinese_law_name.md#第14條' → ('chinese_law_name', '第14條').

    Returns None if the article token can't be found. Law name keeps its
    raw form (filename minus .md) so comparison is exact per law.
    """
    if "#" not in doc:
        return None
    law_part, art_part = doc.split("#", 1)
    law = law_part[:-3] if law_part.endswith(".md") else law_part
    m = _ARTICLE_RE.search(art_part)
    if not m:
        return None
    return law, f"第{m.group(1).strip()}條"


def law_article_set(retrieved_docs: List[str]) -> set:
    """Set of (law, article) tuples from a retrieved_docs list."""
    out = set()
    for d in retrieved_docs or []:
        parsed = parse_retrieved_doc(d)
        if parsed:
            out.add(parsed)
    return out


def expected_law_articles(expected_docs: List[str], expected_articles: List[str]) -> set:
    """Build the law-aware expected set.

    Prefers expected_docs ('<law>.md#第N條', law-qualified). Falls back to
    expected_articles (article-only) when expected_docs is absent — those
    match any law (the pre-existing cross-law ambiguity, flagged in report).
    """
    out = set()
    for d in expected_docs or []:
        parsed = parse_retrieved_doc(d)
        if parsed:
            out.add(parsed)
    if out:
        return out
    # Fallback: article-only, law unknown (sentinel law "*")
    for a in expected_articles or []:
        m = _ARTICLE_RE.search(a)
        if m:
            out.add(("*", f"第{m.group(1).strip()}條"))
    return out


def _article_only(s: set) -> set:
    return {art for _law, art in s}


def _match(expected: set, candidate: set) -> bool:
    """True if any expected (law, article) is present in candidate.

    If expected uses the '*' law sentinel (no expected_docs), fall back to
    article-only comparison (cannot disambiguate law).
    """
    if any(law == "*" for law, _ in expected):
        return bool(_article_only(expected) & _article_only(candidate))
    return bool(expected & candidate)


def attribute_one(
    expected_docs: List[str],
    expected_articles: List[str],
    cited_articles: List[str],
    retrieved_docs: Optional[List[str]],
    *,
    is_hit: bool,
) -> dict:
    """Classify one in-scope query.

    cited_articles are article-only (extracted from the answer text), so the
    cited↔retrieved and cited↔expected comparisons fall back to article-only;
    the law-aware power applies to expected↔retrieved (the R-miss decision),
    which is where the two-law collision actually bites.
    """
    expected = expected_law_articles(expected_docs, expected_articles)
    if not expected:
        return {"label": SKIPPED, "reason": "無 expected ground truth"}

    if retrieved_docs is None:
        return {"label": NO_AUDIT, "reason": "audit log 無對應 query，無法判定檢索層"}

    retrieved = law_article_set(retrieved_docs)
    exp_articles = _article_only(expected)
    ret_articles = _article_only(retrieved)
    cited = {a for a in (cited_articles or [])}

    expected_in_retrieval = _match(expected, retrieved)
    expected_in_citation = bool(exp_articles & cited)
    cited_not_retrieved = bool(cited - ret_articles)

    if is_hit or expected_in_citation:
        label = HIT
        reason = "期望條文已被引用"
    elif not expected_in_retrieval:
        label = R_MISS
        reason = f"期望條文未進最終 context（檢索層失敗）；context={sorted(ret_articles)}"
    else:
        label = G_MISS
        reason = "期望條文在 context 中但未被引用（生成層失敗）"

    return {
        "label": label,
        "reason": reason,
        "expected": sorted(f"{l}#{a}" for l, a in expected),
        "retrieved": sorted(f"{l}#{a}" for l, a in retrieved),
        "cited": sorted(cited),
        "hallucinated_citation": cited_not_retrieved and not is_hit,
    }


def summarize(attributions: List[dict]) -> Dict[str, int]:
    """Count attribution labels (only failures carry R/G labels)."""
    counts: Dict[str, int] = {}
    for a in attributions:
        counts[a["label"]] = counts.get(a["label"], 0) + 1
    return counts
