#!/usr/bin/env python3
"""
Per-step Attribution — 判定每筆失敗在檢索層還是生成層。

ISO 42001 A.6.2.4 V&V 證據工具。join 一份 online V&V 報告（答案側：
cited_articles）與 audit log（檢索側：retrieved_docs = 重排後最終 context），
把每筆 in-scope 查詢分類為 hit / R-miss / G-miss，並標記幻覺引用。

用途：在投入檢索層或生成層改進前，先用硬證據判定失敗根因，避免盲改
（eval_m10 曾連續多版失敗無人能歸因即是此工具要解的問題）。

唯讀；不修改 RAG/ 任何檔案。audit log 經 monitoring 容器 :ro 掛載讀取。

Usage:
    python3 scripts/run_attribution.py \
        --vv-report data/reports/online_vv_2026-06-11.json \
        --audit-dir /rag_data/audit_logs \
        --golden data/golden_dataset.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ADDON_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(ADDON_DIR))

from monitoring.attribution import attribute_one, summarize  # noqa: E402
from monitoring.audit_loader import load_events, list_audit_files  # noqa: E402

_TPE_TZ = timezone(timedelta(hours=8))


def _build_retrieval_index(audit_dir: Path) -> dict:
    """Map normalized user_query → latest retrieved_docs from audit log.

    Latest wins so a re-run's fresh context supersedes stale entries.
    """
    events = load_events(list_audit_files(audit_dir))
    index = {}
    for e in events:
        if e.get("event_type") != "query":
            continue
        q = (e.get("user_query") or "").strip()
        if q:
            index[q] = e.get("retrieved_docs")  # later overwrites earlier
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-step retrieval/generation attribution")
    ap.add_argument("--vv-report", required=True, help="online V&V 報告 JSON（含 per_query）")
    ap.add_argument("--audit-dir", default="/rag_data/audit_logs",
                    help="audit log 目錄（容器內預設 /rag_data/audit_logs）")
    ap.add_argument("--golden", default=str(ADDON_DIR / "data" / "golden_dataset.json"),
                    help="golden dataset（取 expected_docs 做 law-aware 比對）")
    ap.add_argument("--output-dir", default=str(ADDON_DIR / "data" / "reports"))
    args = ap.parse_args()

    try:
        vv = json.loads(Path(args.vv_report).read_text(encoding="utf-8"))
        golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: 讀取失敗：{e}", file=sys.stderr)
        return 2

    per_query = vv.get("per_query")
    if not per_query:
        print("ERROR: V&V 報告缺 per_query", file=sys.stderr)
        return 2

    # golden: query text → expected_docs
    golden_by_id = {g.get("id"): g for g in golden}

    retrieval_index = _build_retrieval_index(Path(args.audit_dir))
    if not retrieval_index:
        print(f"WARN: {args.audit_dir} 無 query 事件，所有題目將為 no-audit-match",
              file=sys.stderr)

    attributions = []
    for r in per_query:
        if r.get("category") == "out_of_scope":
            continue
        qid = r.get("id")
        query = (r.get("query") or "").strip()
        g = golden_by_id.get(qid, {})
        expected_docs = g.get("expected_docs", [])
        retrieved = retrieval_index.get(query)  # None if no audit match
        hr = r.get("hit_rate")
        is_hit = hr is not None and hr > 0
        a = attribute_one(
            expected_docs=expected_docs,
            expected_articles=r.get("expected_articles", []),
            cited_articles=r.get("cited_articles", []),
            retrieved_docs=retrieved,
            is_hit=is_hit,
        )
        a.update({"id": qid, "query": query, "category": r.get("category"),
                  "difficulty": r.get("difficulty")})
        attributions.append(a)

    counts = summarize(attributions)
    failures = [a for a in attributions if a["label"] in ("R-miss", "G-miss")]

    now = datetime.now(_TPE_TZ)
    report = {
        "generated_at": now.isoformat(),
        "vv_report": str(args.vv_report),
        "counts": counts,
        "failures": failures,
        "attributions": attributions,
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    json_path = out_dir / f"attribution_{date_str}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Per-step 歸因結果 ===")
    print(f"分類統計：{counts}")
    if failures:
        print("\n失敗案例歸因：")
        for f in failures:
            print(f"  [{f['label']}] {f['id']} ({f.get('difficulty')}): {f['query'][:36]}")
            print(f"        {f['reason']}")
            print(f"        expected={f.get('expected')} cited={f.get('cited')}")
    else:
        print("（無 R-miss/G-miss 失敗，或無 audit 比對）")
    print(f"\nJSON: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
