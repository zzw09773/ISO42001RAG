#!/usr/bin/env python3
"""
線上 V&V — actually call the RAG API and compute true Hit Rate.

Unlike scripts/run_extended_vv.py which is offline (no API call), this script
sends each golden-dataset query to the running RAG API, parses the cited
articles out of the LLM's answer, and compares against `expected_articles`
to compute the real Hit Rate.

This is the only path to verifying the business goal: Hit Rate ≥ 0.90.

Why parse cited articles instead of retrieved_docs?
  RAG/api.py does NOT log retrieved_docs to audit log (the field is always [])
  — this is a main-system limitation we cannot change during the audit freeze.
  Instead we extract "第X條" mentions from the LLM's natural-language answer,
  which is a stronger signal anyway: it proves the answer actually USED the
  relevant article, not just retrieved it.

Out-of-scope handling:
  Queries with category="out_of_scope" are evaluated against rejection
  behaviour — the answer must contain the rejection phrase. These contribute
  to `rejection_accuracy` but NOT to the main Hit Rate denominator.

Usage:
    python3 scripts/run_online_vv.py
    python3 scripts/run_online_vv.py --rag-url http://localhost:8043 --limit 5
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.baseline_loader import default_golden_path, load_golden_dataset
from monitoring.config import BUSINESS_GOAL_HIT_RATE
from monitoring.ir_metrics import (
    compute_extended_metrics,
    hit_rate,
    precision_at_k,
    recall_at_k,
    f1_at_k,
)

try:
    import requests
except ImportError:
    print("ERROR: requests is required. pip install requests", file=sys.stderr)
    sys.exit(2)


# Matches both arabic-number and Chinese-number article references
_ARTICLE_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百零兩]+)\s*條")
_REJECTION_PREFIXES = (
    "本系統僅提供法律文件",
    "無法回答與法律無關的問題",
    "抱歉，本系統",
)

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "兩": 2,
}


def _cn_to_arabic(num_str: str) -> str:
    """Convert a Chinese numeral string ('十五', '一百二十', etc.) to arabic.

    Returns the arabic string if conversion succeeds, otherwise returns
    the input unchanged. Already-arabic input ('15', '46') is returned as-is.
    Supports the range needed for legal article numbers (1–999).
    """
    if num_str.isdigit():
        return num_str  # already arabic
    if not any(ch in num_str for ch in "零一二三四五六七八九十百兩"):
        return num_str

    total = 0
    section = 0  # running ones+tens value
    for ch in num_str:
        if ch == "百":
            total += (section or 1) * 100
            section = 0
        elif ch == "十":
            section = (section or 1) * 10
        elif ch in _CN_DIGITS:
            section += _CN_DIGITS[ch]
        else:
            return num_str  # unknown char, give up
    total += section
    return str(total) if total > 0 else num_str


def normalize_article(token: str) -> str:
    """Normalize '第 46 條' / '第46條' / '第四十六條' / '第十五條' to '第N條'.

    Canonical form: '第N條' (arabic, no spaces). Chinese numerals ARE
    converted to arabic so '第十五條' and '第15條' compare as equal —
    this fixes the false-negative that previously misclassified eval_m11.
    """
    m = _ARTICLE_RE.search(token)
    if not m:
        return token.strip()
    return f"第{_cn_to_arabic(m.group(1).strip())}條"


def extract_cited_articles(answer: str) -> List[str]:
    """Pull every article reference from the LLM answer, dedup, canonicalize.

    Chinese-numeral mentions are converted to arabic so they merge with
    their arabic-numeral counterparts (e.g., '第十五條' and '第15條' count
    as the same article).
    """
    cited = []
    seen = set()
    for m in _ARTICLE_RE.finditer(answer):
        canon = f"第{_cn_to_arabic(m.group(1).strip())}條"
        if canon not in seen:
            cited.append(canon)
            seen.add(canon)
    return cited


def is_rejection(answer: str) -> bool:
    return any(p in answer for p in _REJECTION_PREFIXES)


def ask_rag(rag_url: str, query: str, *, api_key: Optional[str], timeout: float) -> str:
    """Call /v1/chat/completions, return answer text. Returns '' on error."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": "rag-agent",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    try:
        r = requests.post(
            f"{rag_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  ERROR calling RAG: {e}", file=sys.stderr)
        return ""


def evaluate_one(entry: dict, answer: str) -> dict:
    """Score a single query against its golden ground truth."""
    expected = [normalize_article(a) for a in entry.get("expected_articles", [])]
    cited = extract_cited_articles(answer)
    is_oos = entry.get("category") == "out_of_scope"
    rejected = is_rejection(answer)

    record = {
        "id": entry["id"],
        "query": entry["query"],
        "category": entry.get("category"),
        "difficulty": entry.get("difficulty"),
        "expected_articles": expected,
        "cited_articles": cited,
        "is_rejection_correct": None,  # set below if oos
        "hit_rate": None,
        "precision@k": None,
        "recall@k": None,
        "f1@k": None,
    }

    if is_oos:
        record["is_rejection_correct"] = rejected
        return record

    # in-scope: standard IR metrics on cited vs expected articles
    if not expected:
        # in-scope but golden didn't specify articles — skip metric
        return record

    record["hit_rate"] = hit_rate(cited, expected)
    record["precision@k"] = precision_at_k(cited, expected, k=5)
    record["recall@k"] = recall_at_k(cited, expected, k=5)
    record["f1@k"] = f1_at_k(cited, expected, k=5)
    return record


def aggregate(records: List[dict]) -> dict:
    """Roll up per-query records into aggregate metrics."""
    in_scope = [r for r in records if r["category"] != "out_of_scope" and r["hit_rate"] is not None]
    oos = [r for r in records if r["category"] == "out_of_scope"]

    n = len(in_scope)
    metrics = {"evaluated": n, "skipped": len(records) - n - len(oos), "k": 5}
    if n:
        metrics["hit_rate"] = sum(r["hit_rate"] for r in in_scope) / n
        metrics["precision_at_k"] = sum(r["precision@k"] for r in in_scope) / n
        metrics["recall_at_k"] = sum(r["recall@k"] for r in in_scope) / n
        metrics["f1_at_k"] = sum(r["f1@k"] for r in in_scope) / n
    else:
        metrics["hit_rate"] = 0.0
        metrics["precision_at_k"] = 0.0
        metrics["recall_at_k"] = 0.0
        metrics["f1_at_k"] = 0.0
    metrics["mrr"] = 0.0  # not meaningful for cited-article matching
    metrics["map"] = 0.0

    oos_total = len(oos)
    oos_correct = sum(1 for r in oos if r["is_rejection_correct"])
    rejection_accuracy = oos_correct / oos_total if oos_total else None

    return {
        "metrics": {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()},
        "oos_total": oos_total,
        "oos_correct_rejections": oos_correct,
        "rejection_accuracy": round(rejection_accuracy, 4) if rejection_accuracy is not None else None,
    }


def render_markdown(report: dict) -> str:
    m = report["aggregate"]["metrics"]
    bg = report["business_goal"]
    badge = {"met": "✅ 目標達成", "not_met": "❌ 目標未達", "inconclusive": "⚠️ 尚未驗證"}[bg["status"]]
    rej_acc = report["aggregate"]["rejection_accuracy"]

    lines = [
        "# ISO 42001 A.6 線上 V&V 報告（實打 RAG API 計算 Hit Rate）",
        "",
        f"**產生時間**：{report['generated_at']}",
        f"**RAG API**：{report['rag_url']}",
        f"**資料集**：{report['dataset_size']} 筆（in-scope: {m['evaluated']}，out-of-scope: {report['aggregate']['oos_total']}）",
        f"**業務目標**：Hit Rate ≥ {bg['target']}",
        f"**達標狀態**：{badge}",
        "",
        "## 主指標（in-scope 查詢）",
        "",
        "| 指標 | 分數 | 是否 gating |",
        "|------|------|-------------|",
        f"| **Hit Rate** | **{m['hit_rate']}** | ✅ 業務目標 |",
        f"| Precision@K | {m['precision_at_k']} | info |",
        f"| Recall@K | {m['recall_at_k']} | info |",
        f"| F1@K | {m['f1_at_k']} | info |",
        f"| 評估筆數 | {m['evaluated']} | — |",
        "",
        "## Out-of-Scope 拒絕測試",
        "",
        f"- 應拒絕筆數：{report['aggregate']['oos_total']}",
        f"- 正確拒絕筆數：{report['aggregate']['oos_correct_rejections']}",
        f"- 拒絕正確率：{rej_acc if rej_acc is not None else 'N/A'}",
        "",
        "## 失敗案例（hit_rate = 0）",
        "",
    ]
    fails = [r for r in report["per_query"] if r.get("hit_rate") == 0.0]
    if fails:
        for r in fails[:15]:
            lines.append(f"- `{r['id']}` 「{r['query'][:40]}...」 expected={r['expected_articles']} cited={r['cited_articles']}")
    else:
        lines.append("（無失敗案例）")
    lines += [
        "",
        "---",
        f"*由 `monitoring_addon/scripts/run_online_vv.py` 自動產生。本腳本獨立於 RAG 主系統，不修改任何 RAG/ 內檔案。*",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="線上 V&V: real Hit Rate via RAG API")
    ap.add_argument("--rag-url", default=os.environ.get("RAG_API_URL", "http://localhost:8043"),
                    help="RAG API base URL（容器內由 RAG_API_URL=http://rag-api:8000 提供；host 預設 localhost:8043）")
    ap.add_argument("--api-key", default=None, help="Bearer token (omit for intranet mode)")
    ap.add_argument("--golden", default=None, help="Golden dataset path")
    ap.add_argument("--timeout", type=float, default=60.0, help="Per-query timeout (sec)")
    ap.add_argument("--limit", type=int, default=0, help="Only run first N queries (0 = all)")
    ap.add_argument("--sleep-ms", type=int, default=200, help="Sleep between queries (avoid rate limit)")
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "reports"),
    )
    args = ap.parse_args()

    golden_path = Path(args.golden) if args.golden else default_golden_path()
    dataset = load_golden_dataset(golden_path)
    if args.limit > 0:
        dataset = dataset[: args.limit]
    print(f"Loaded {len(dataset)} entries from {golden_path}")
    print(f"Calling RAG API at {args.rag_url} ...\n")

    records: List[dict] = []
    for i, entry in enumerate(dataset, start=1):
        print(f"[{i}/{len(dataset)}] {entry['id']} :: {entry['query'][:48]}")
        answer = ask_rag(args.rag_url, entry["query"], api_key=args.api_key, timeout=args.timeout)
        rec = evaluate_one(entry, answer)
        rec["answer_preview"] = answer[:200]
        records.append(rec)
        hit_str = "—" if rec["hit_rate"] is None else f"hit={rec['hit_rate']}"
        cited_str = ",".join(rec["cited_articles"][:3]) or "—"
        print(f"     {hit_str}  cited={cited_str}")
        time.sleep(args.sleep_ms / 1000)

    agg = aggregate(records)
    hit_rate_val = agg["metrics"]["hit_rate"]
    if agg["metrics"]["evaluated"] == 0:
        status = "inconclusive"
    elif hit_rate_val >= BUSINESS_GOAL_HIT_RATE:
        status = "met"
    else:
        status = "not_met"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rag_url": args.rag_url,
        "dataset_path": str(golden_path),
        "dataset_size": len(dataset),
        "business_goal": {
            "metric": "hit_rate",
            "target": BUSINESS_GOAL_HIT_RATE,
            "current": hit_rate_val,
            "status": status,
        },
        "aggregate": agg,
        "per_query": records,
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = out_dir / f"online_vv_{date_str}.json"
    md_path = out_dir / f"online_vv_{date_str}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    # Also write to vv_report shape so dashboard_data picks it up as the V&V snapshot.
    # NOTE: avg_article_match is intentionally 0.0 here because drift_detector
    # treats it as the *baseline* for the audit-log citation_rate (count of
    # query events whose citation_count > 0). The two metrics use DIFFERENT
    # signals (V&V uses cited article overlap with expected_articles; audit
    # logs use citation_count which RAG/api.py currently doesn't populate).
    # Writing hit_rate into avg_article_match here would make drift report a
    # phantom "-0.9355" citation_rate gap, confusing auditors.
    vv_snapshot = {
        "generated_at": report["generated_at"],
        "dataset_size": report["dataset_size"],
        "retrieval": {
            "hit_rate": agg["metrics"]["hit_rate"],
            "precision_at_k": agg["metrics"]["precision_at_k"],
            "recall_at_k": agg["metrics"]["recall_at_k"],
            "f1_at_k": agg["metrics"]["f1_at_k"],
            "mrr": 0.0,
            "evaluated": agg["metrics"]["evaluated"],
            "skipped": agg["metrics"]["skipped"],
        },
        "answer_quality_baseline": {
            # Keep at 0.0 — see comment above. dashboard_data still reads
            # `retrieval.hit_rate` for the business-goal card.
            "avg_article_match": 0.0,
        },
        "rejection_accuracy": agg["rejection_accuracy"],
        "source": "online_vv",
    }
    vv_path = out_dir / f"vv_report_{date_str}.json"
    vv_path.write_text(json.dumps(vv_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nJSON:           {json_path}")
    print(f"Markdown:       {md_path}")
    print(f"V&V snapshot:   {vv_path}")
    print(f"\nBusiness goal: Hit Rate {hit_rate_val} vs target {BUSINESS_GOAL_HIT_RATE} → {status.upper()}")
    return 0 if status == "met" else 1


if __name__ == "__main__":
    sys.exit(main())
