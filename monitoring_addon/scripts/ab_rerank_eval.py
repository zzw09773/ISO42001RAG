#!/usr/bin/env python3
"""
A/B test: does the LLM rerank step actually improve retrieval quality?

For each query in the golden dataset we:
  A. Call RetrievalService.query() normally (LLM rerank ON)
  B. Monkey-patch _rerank_top_n_with_llm to a no-op (rerank OFF — return
     hybrid-search candidates by original score order, truncated to top_n)
and compare:
  - Hit Rate         : does retrieval surface ANY expected_article?
  - Precision@K      : fraction of retrieved that match expected_articles
  - Recall@K         : fraction of expected_articles found in retrieved
  - Avg latency (ms) : retrieval time per query

This is a STRICTLY retrieval-layer test — generate/verify are not invoked,
so LLM answer-parsing noise doesn't pollute the comparison. The only
variable is the rerank step.

Usage:
    python3 monitoring_addon/scripts/ab_rerank_eval.py
    python3 monitoring_addon/scripts/ab_rerank_eval.py --limit 10
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Set

# Repo path so we can import the RAG source (mounted at /app in container)
sys.path.insert(0, "/app")

from rag_system.core.config import RAGConfig
from rag_system.services.retrieval import RetrievalService


# Match both arabic and Chinese-number article references in metadata
_ARTICLE_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零兩]+)\s*條")


def _extract_article_id(doc) -> str:
    """Return canonical '第N條' label from a retrieved doc, or ''."""
    aid = doc.metadata.get("article_id", "") if hasattr(doc, "metadata") else ""
    if aid and aid not in ("preamble", "whole_document"):
        # article_id is already like '第5條' or '5'
        if aid.startswith("第"):
            return aid
        # Sometimes stored as bare number
        return f"第{aid}條"
    # Fallback: parse page_content for the FIRST article reference
    content = getattr(doc, "page_content", "") or ""
    m = _ARTICLE_RE.search(content)
    if m:
        return f"第{m.group(1)}條"
    return ""


def evaluate(svc: RetrievalService, golden: list, label: str) -> dict:
    """Run all golden queries through svc.query() and aggregate metrics."""
    hits = 0
    precisions: list = []
    recalls: list = []
    latencies_ms: list = []
    per_query: list = []
    for entry in golden:
        if entry.get("category") == "out_of_scope":
            continue  # rerank doesn't affect rejection logic
        question = entry.get("query") or ""
        expected: Set[str] = set(entry.get("expected_articles") or [])
        if not question or not expected:
            continue

        t0 = time.monotonic()
        try:
            docs = svc.query(question)
        except Exception as e:
            docs = []
            print(f"  ERROR on '{question[:30]}': {e}", file=sys.stderr)
        latency_ms = int((time.monotonic() - t0) * 1000)
        latencies_ms.append(latency_ms)

        retrieved_articles = {_extract_article_id(d) for d in docs}
        retrieved_articles.discard("")
        match = expected & retrieved_articles
        hit = bool(match)
        if hit:
            hits += 1
        p = len(match) / max(len(retrieved_articles), 1)
        r = len(match) / max(len(expected), 1)
        precisions.append(p)
        recalls.append(r)
        per_query.append({
            "q": question,
            "expected": sorted(expected),
            "retrieved": sorted(retrieved_articles),
            "hit": hit,
            "p": round(p, 3),
            "r": round(r, 3),
            "latency_ms": latency_ms,
        })

    n = len(precisions)
    return {
        "label": label,
        "n": n,
        "hit_rate": round(hits / n, 4) if n else 0.0,
        "precision_at_k": round(sum(precisions) / n, 4) if n else 0.0,
        "recall_at_k": round(sum(recalls) / n, 4) if n else 0.0,
        "f1_at_k": (
            round(2 * (sum(precisions)/n) * (sum(recalls)/n) /
                  ((sum(precisions)/n) + (sum(recalls)/n)), 4)
            if n and (sum(precisions) + sum(recalls)) > 0 else 0.0
        ),
        "avg_latency_ms": int(sum(latencies_ms) / n) if n else 0,
        "p95_latency_ms": (
            sorted(latencies_ms)[int(0.95 * (n - 1))] if n else 0
        ),
        "per_query": per_query,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="/app/data/golden_dataset.json",
                    help="Golden dataset JSON path")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit queries (for quick smoke test)")
    ap.add_argument("--output", default=None,
                    help="Write JSON report here")
    args = ap.parse_args()

    # Container path mapping: monitoring_addon/data/golden_dataset.json
    # maps to /app/data/golden_dataset.json INSIDE monitoring container,
    # but this script runs in rag-api container — adjust to either mount.
    golden_paths = [
        Path(args.golden),
        Path("/app/tests/evaluation/golden_dataset.json"),
        Path("/app/monitoring_addon/data/golden_dataset.json"),
    ]
    golden_path = next((p for p in golden_paths if p.exists()), None)
    if not golden_path:
        print(f"ERROR: golden dataset not found in any of {golden_paths}",
              file=sys.stderr)
        sys.exit(2)
    print(f"Golden dataset: {golden_path}")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    if args.limit:
        golden = golden[:args.limit]
    print(f"Evaluating {len(golden)} entries\n")

    cfg = RAGConfig.from_env()

    # ── A. With LLM rerank (current default) ──────────────────────────────
    svc_a = RetrievalService(cfg)
    print("▶ A. LLM rerank ON  (current default)")
    a = evaluate(svc_a, golden, "rerank_on")

    # ── B. Without LLM rerank (monkey-patch to identity-truncate) ─────────
    svc_b = RetrievalService(cfg)
    original = svc_b._rerank_top_n_with_llm

    def _no_rerank(question, summaries, top_n):
        # Return top N candidates by ORIGINAL hybrid-search score order
        return summaries[:top_n]

    svc_b._rerank_top_n_with_llm = _no_rerank
    print("\n▶ B. LLM rerank OFF (hybrid-search top-N by score)")
    b = evaluate(svc_b, golden, "rerank_off")

    # Restore (not strictly needed, instance is local)
    svc_b._rerank_top_n_with_llm = original

    # ── Comparison table ──────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("RERANK A/B — pure retrieval layer (no LLM generation)")
    print("=" * 64)
    fmt = "{:<18}  {:>10}  {:>10}  {:>10}"
    print(fmt.format("metric", "ON", "OFF", "delta(ON-OFF)"))
    print("-" * 64)
    for k, name in [
        ("hit_rate",       "Hit Rate"),
        ("precision_at_k", "Precision@K"),
        ("recall_at_k",    "Recall@K"),
        ("f1_at_k",        "F1@K"),
        ("avg_latency_ms", "Avg latency ms"),
        ("p95_latency_ms", "P95 latency ms"),
    ]:
        on_v, off_v = a[k], b[k]
        delta = on_v - off_v if isinstance(on_v, (int, float)) else "-"
        delta_str = f"{delta:+.4f}" if isinstance(delta, float) else f"{delta:+d}"
        print(fmt.format(name, f"{on_v}", f"{off_v}", delta_str))
    print("-" * 64)
    print(f"N queries (in-scope, with expected_articles) = {a['n']}")

    if args.output:
        Path(args.output).write_text(
            json.dumps({"A_rerank_on": a, "B_rerank_off": b}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nFull JSON: {args.output}")


if __name__ == "__main__":
    main()
