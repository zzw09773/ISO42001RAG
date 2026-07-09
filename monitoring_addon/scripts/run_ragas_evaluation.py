#!/usr/bin/env python3
"""
RAGAS Evaluation — Faithfulness / Answer Relevancy / Context Precision

Replays the golden dataset through the running RAG API, then uses an LLM
to score each (query, retrieved_docs, answer) tuple on three RAGAS-style
metrics. Outputs JSON + Markdown reports.

This complements (does NOT replace) `run_online_vv.py`:
  - online_vv : structural metrics (Hit Rate, Precision@K, Recall@K)
  - ragas     : answer-grounding quality (Faithfulness etc.)

For ISO 42001 A.9 V&V auditing, both are needed: hit rate proves the
system retrieves the right docs; RAGAS proves the answer is grounded in
those docs and actually addresses the question.

Usage:
    python3 scripts/run_ragas_evaluation.py
    python3 scripts/run_ragas_evaluation.py --limit 10  # cheaper sample
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from monitoring.baseline_loader import default_golden_path, load_golden_dataset
from monitoring.article_resolver import load_article_index, resolve_context
from monitoring.ragas_metrics import (
    aggregate_ragas_scores,
    score_single,
    RagasScore,
    _call_llm,
    _parse_json,
)


THRESHOLDS = {
    "faithfulness": 0.70,
    "answer_relevancy": 0.70,
    "context_precision": 0.50,
    "overall": 0.65,
}

# RAG/api.py 的 ANSWER_DISCLAIMER（程式保證附加的 A.9 使用聲明）。
# faithfulness 評「答案是否被檢索文件支撐」，聲明本就不在條文中，
# 評分前剝除以免每題被固定扣分。措辭變更須與 RAG/api.py 同步。
_DISCLAIMER_MARK = "本回答由 AI 依知識庫收錄之法規文件生成"


def strip_answer_disclaimer(answer: str) -> str:
    """剝除答案末尾由程式附加的固定使用聲明（含其前的 --- 分隔線）。"""
    idx = answer.find(_DISCLAIMER_MARK)
    if idx == -1:
        return answer
    head = answer[:idx].rstrip()
    if head.endswith("---"):
        head = head[:-3].rstrip()
    return head


def preflight(rag_url: str) -> Optional[str]:
    """Verify the RAG API and the LLM judge are reachable BEFORE evaluating.

    Returns an error string if anything is unreachable; the caller then aborts
    WITHOUT writing a report, so a connectivity outage never lands a bogus
    faithfulness=0.0/None report on the dashboard. (P1-0)
    """
    try:
        requests.get(f"{rag_url}/health", timeout=10).raise_for_status()
    except Exception as e:
        return (f"RAG API 不可達：{rag_url}/health（{e}）。"
                f"\n   容器內請用服務名 --rag-url http://rag-api:8000（非 localhost:8043，那是 host port）。")

    if not os.environ.get("LLM_API_BASE"):
        return ("找不到 LLM_API_BASE——judge 無法連線。"
                "\n   請在 monitoring 服務環境提供 LLM_API_BASE 與 CHAT_MODEL_NAME。")

    if _parse_json(_call_llm('只回傳 JSON：{"ok": 1}')) is None:
        base = os.environ.get("LLM_API_BASE")
        return (f"LLM judge 無有效 JSON 回應（base={base}, model={os.environ.get('CHAT_MODEL_NAME', 'gpt-oss-20b')}）。"
                "\n   請確認 LLM_API_BASE 指向 chat 端點（非 embedding proxy）且金鑰正確。")
    return None


def ask_rag_with_context(rag_url: str, query: str, timeout: float = 90.0, article_index=None) -> dict:
    """Call RAG API and return both the answer AND the retrieved context.

    Pulls retrieved_docs from the most recent audit log entry matching
    this session — that's how `audit log retrieved_docs补强 (v1.0.2)` paid
    off here: we get the actual retrieved sources without parsing the
    answer.
    """
    session_id = f"ragas-{int(time.time() * 1000)}"
    payload = {
        "model": "rag-agent",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    try:
        r = requests.post(
            f"{rag_url}/v1/chat/completions",
            headers={"Content-Type": "application/json", "x-session-id": session_id},
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        answer = data["choices"][0]["message"]["content"]
        answer = strip_answer_disclaimer(answer)
    except Exception as e:
        return {"answer": "", "context": "", "retrieved_docs": [], "error": str(e)}

    # Pull retrieved_docs from the latest audit log entry for this session
    retrieved_docs: List[str] = []
    context_text = ""
    import os as _os
    rag_data_dir = _os.environ.get("RAG_DATA_DIR", "../RAG/data")
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = Path(rag_data_dir) / "audit_logs" / f"audit_{today}.jsonl"
    if log_path.exists():
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in reversed(f.readlines()):
                    rec = json.loads(line)
                    if rec.get("session_id") == session_id:
                        retrieved_docs = rec.get("retrieved_docs", []) or []
                        break
        except Exception:
            pass

    # Resolve retrieved_docs labels to REAL article text so Faithfulness judges
    # answer-vs-content (not answer-vs-label). Falls back to labels if the
    # converted_md isn't readable. article_index is passed in for reuse.
    if article_index:
        context_text = resolve_context(retrieved_docs, article_index)
    else:
        context_text = "\n".join(f"- {d}" for d in retrieved_docs)

    return {
        "answer": answer,
        "context": context_text,
        "retrieved_docs": retrieved_docs,
    }


def render_markdown(report: dict) -> str:
    s = report["aggregate"]
    bg = report["business_threshold_check"]

    def state(v, t):
        if not isinstance(v, (int, float)):
            return "—"
        return "✅" if v >= t else "❌"

    def fmt(v):
        return v if isinstance(v, (int, float)) else "N/A（未評估）"

    lines = [
        "# ISO 42001 A.9 — RAGAS Evaluation Report",
        "",
        f"**產生時間**：{report['generated_at']}",
        f"**RAG API**：{report['rag_url']}",
        f"**Judge 模型**：{report.get('judge_model', 'gpt-oss-20b')}",
        f"**樣本數**：{s['sample_size']}（跳過：{s['skipped']}）",
        f"**Faithfulness 評估**：實評 {s.get('faithfulness_evaluated', '?')} 題"
        f"；回應分類 {s.get('faithfulness_breakdown', {})}"
        f"；in-scope 拒答率（疑似漏接）{fmt(s.get('no_coverage_rate'))}",
        "",
        "> Faithfulness 平均**只計入 judge 實際評分的題目**；judge 連不上時顯示「N/A（未評估）」而非 0.0。",
        "> 拒答（「無相關資料」）一律**排除於 faithfulness**（無主張可驗證），既不計 1.0 也不計 0.0。"
        "本評估已濾除 out_of_scope，golden 全為「應可回答」題，故此處**拒答率＝應答卻拒答＝疑似漏接**，"
        "請對照 online_vv 的 Hit Rate / Recall 判讀（與『正確拒絕無關問題』是不同軸，後者由 OOS 測試集衡量）。",
        "> 註：報告中若出現 `rejection_oos`（系統把 in-scope 問題誤判為無關而拒絕）屬**錯誤拒答、應為 0**；"
        "`no_coverage`＝in-scope 查無條文（計入上述拒答率）。",
        "",
        "## RAGAS 三大指標（LLM-as-judge）",
        "",
        "| 指標 | 分數 | 門檻 | 狀態 | 含意 |",
        "|------|------|------|------|------|",
        f"| **Faithfulness** | {fmt(s['faithfulness'])} | {THRESHOLDS['faithfulness']} | {state(s['faithfulness'], THRESHOLDS['faithfulness'])} | 答案主張是否來自檢索文件（不幻覺；已排除拒答） |",
        f"| **Answer Relevancy** | {fmt(s['answer_relevancy'])} | {THRESHOLDS['answer_relevancy']} | {state(s['answer_relevancy'], THRESHOLDS['answer_relevancy'])} | 答案是否真的回應問題（不偏題） |",
        f"| **Context Precision** | {fmt(s['context_precision'])} | {THRESHOLDS['context_precision']} | {state(s['context_precision'], THRESHOLDS['context_precision'])} | 檢索文件是否真的被答案用到 |",
        f"| **Overall** | **{fmt(s['overall'])}** | **{THRESHOLDS['overall']}** | {state(s['overall'], THRESHOLDS['overall'])} | 已評估指標等權平均 |",
        "",
        f"## 業務門檻檢查",
        "",
        f"整體狀態：{'✅ PASS' if bg['passed'] else '❌ FAIL'}",
        f"理由：{bg['reason']}",
        "",
        "## 與 online_vv 的關係",
        "",
        "- `online_vv` 證明「**檢索到正確文件**」（Hit Rate, Recall）",
        "- `ragas` 證明「**答案基於這些文件且回應問題**」（Faithfulness, Answer Relevancy）",
        "- 兩者並用為 ISO 42001 A.9 V&V 提供完整證據鏈",
        "",
        "---",
        "*本報告由 `monitoring_addon/scripts/run_ragas_evaluation.py` 自動產生，作為 ISO 42001 A.9 V&V 補強證據（answer-grounding 品質）。*",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS evaluation (Faithfulness / Relevancy / Context Precision)")
    ap.add_argument("--rag-url", default=os.environ.get("RAG_API_URL", "http://localhost:8043"),
                    help="RAG API base URL（容器內由 RAG_API_URL=http://rag-api:8000 提供；host 預設 localhost:8043）")
    ap.add_argument("--golden", default=None, help="Golden dataset path")
    ap.add_argument("--limit", type=int, default=0, help="Only first N entries (0 = all)")
    ap.add_argument("--skip-oos", action="store_true", default=True, help="Skip out-of-scope entries (default)")
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "reports"),
    )
    args = ap.parse_args()

    err = preflight(args.rag_url)
    if err:
        print(f"❌ 前置連線檢查失敗：{err}", file=sys.stderr)
        print("   未執行評估、未寫入任何報告（避免假性 faithfulness 落到儀表板）。", file=sys.stderr)
        return 2

    golden_path = Path(args.golden) if args.golden else default_golden_path()
    dataset = load_golden_dataset(golden_path)
    if args.skip_oos:
        dataset = [e for e in dataset if e.get("category") != "out_of_scope"]
    if args.limit > 0:
        dataset = dataset[: args.limit]
    print(f"Loaded {len(dataset)} entries (OOS skipped: {args.skip_oos})")
    print(f"RAG API: {args.rag_url}\n")

    # Parse the law corpus once so Faithfulness judges against REAL article text.
    article_index = load_article_index()
    print(f"條文索引：解析 {len(article_index)} 條（faithfulness 將用真實條文內容評估）"
          if article_index else "⚠ 找不到 converted_md，faithfulness 退回標籤級評估\n")

    per_query = []
    for i, entry in enumerate(dataset, start=1):
        q = entry["query"]
        print(f"[{i}/{len(dataset)}] {entry['id']} :: {q[:48]}")
        sample = ask_rag_with_context(args.rag_url, q, article_index=article_index)
        if not sample["answer"]:
            print("     ⚠️  empty answer, skip")
            per_query.append({"id": entry["id"], "scores": {
                "faithfulness": {"status": "unavailable", "score": None,
                                 "grounded": None, "ungrounded": None},
                "answer_relevancy": None, "context_precision": None}})
            continue

        scores = score_single(q, sample["context"], sample["retrieved_docs"], sample["answer"])
        per_query.append({
            "id": entry["id"],
            "query": q,
            "answer_preview": sample["answer"][:150],
            "retrieved_docs": sample["retrieved_docs"],
            "scores": scores,
        })
        _f = scores["faithfulness"]
        print(f"     F={_f.get('score')}({_f.get('status')}) "
              f"R={scores['answer_relevancy']} CP={scores['context_precision']}")

    score_dicts = [r["scores"] for r in per_query]
    agg = aggregate_ragas_scores(score_dicts)
    agg_d = agg.to_dict()

    # Business threshold check (None-safe: an unmeasured metric never "passes").
    def _meets(v, t):
        return isinstance(v, (int, float)) and v >= t

    evaluated = agg_d["sample_size"] - agg_d["skipped"]
    passed = (
        _meets(agg_d["faithfulness"], THRESHOLDS["faithfulness"])
        and _meets(agg_d["answer_relevancy"], THRESHOLDS["answer_relevancy"])
        and _meets(agg_d["overall"], THRESHOLDS["overall"])
    )
    if evaluated == 0:
        threshold_check = {"passed": False, "reason": "無有效樣本——judge 對所有查詢皆未產生分數（評估器可能未連上）。"}
    elif agg_d["faithfulness"] is None:
        threshold_check = {"passed": False, "reason": (
            f"Faithfulness 未評估（實評={agg_d['faithfulness_evaluated']}, "
            f"回應分類={agg_d.get('faithfulness_breakdown')}）。")}
    elif passed:
        threshold_check = {"passed": True, "reason": "All thresholds met"}
    else:
        threshold_check = {"passed": False, "reason": (
            f"至少一項低於門檻（overall={agg_d['overall']}, faithfulness={agg_d['faithfulness']}）。")}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rag_url": args.rag_url,
        "dataset_path": str(golden_path),
        "judge_model": os.environ.get("CHAT_MODEL_NAME", "gpt-oss-20b"),
        "judge_prompt": "faithfulness_v2_abstention",
        "thresholds": THRESHOLDS,
        "aggregate": agg_d,
        "business_threshold_check": threshold_check,
        "per_query": per_query,
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = out_dir / f"ragas_{date_str}.json"
    md_path = out_dir / f"ragas_{date_str}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"\nJSON:     {json_path}")
    print(f"Markdown: {md_path}")
    print(f"\nResult: {'✅ PASS' if passed else '❌ FAIL'} | overall={agg_d['overall']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
