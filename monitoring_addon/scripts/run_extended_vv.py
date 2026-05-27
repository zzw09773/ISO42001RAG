#!/usr/bin/env python3
"""
Extended V&V — adds Recall@K, F1@K and MAP to the standard RAG V&V suite.

This script does NOT modify RAG/scripts/run_vv_evaluation.py. It reads the
same golden dataset and writes its own report to monitoring_addon/data/reports/.

If the dataset entries carry no `expected_docs`, retrieval metrics are
marked INCONCLUSIVE (matching the main system's behaviour).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.baseline_loader import default_golden_path, load_golden_dataset
from monitoring.config import BUSINESS_GOAL_HIT_RATE
from monitoring.ir_metrics import compute_extended_metrics


# Business goal: Hit Rate ≥ BUSINESS_GOAL_HIT_RATE is the *only* V&V gating
# metric. Other IR metrics are kept as informational thresholds at lower
# bars — they are reported and flagged but do not block the V&V status.

THRESHOLDS = {
    "hit_rate": BUSINESS_GOAL_HIT_RATE,  # ← gate: business goal
    "precision_at_k": 0.50,              # informational
    "recall_at_k": 0.50,                 # informational
    "f1_at_k": 0.50,                     # informational
    "mrr": 0.40,                         # informational
    "map": 0.40,                         # informational
}

# Only Hit Rate counts toward pass/fail. Others are shown but not gated.
GATING_METRICS = {"hit_rate"}


def evaluate(dataset: list, k: int = 5) -> dict:
    results = [
        {
            "retrieved_docs": entry.get("retrieved_docs", []),
            "expected_docs": entry.get("expected_docs", []),
        }
        for entry in dataset
    ]
    metrics = compute_extended_metrics(results, k=k)
    return metrics.to_dict()


def vv_status(metrics: dict) -> str:
    if metrics["evaluated"] == 0:
        return "inconclusive"
    # Only gating metrics determine pass/fail (business goal = Hit Rate)
    if all(metrics[key] >= THRESHOLDS[key] for key in GATING_METRICS if key in metrics):
        return "pass"
    return "fail"


def render_markdown(report: dict) -> str:
    m = report["metrics"]
    status = report["status"]
    badge = {"pass": "✅ PASS", "fail": "❌ FAIL", "inconclusive": "⚠️ INCONCLUSIVE"}[status]
    inconclusive = m["evaluated"] == 0

    def state(val, thr):
        if inconclusive:
            return "⚠️"
        return "✅" if val >= thr else "❌"

    hit_state = state(m['hit_rate'], THRESHOLDS['hit_rate']) if not inconclusive else "⚠️"
    lines = [
        "# ISO 42001 A.6 擴充 V&V 評估報告（含 Recall@K / F1@K / MAP）",
        "",
        f"**產生時間**：{report['generated_at']}",
        f"**資料集筆數**：{report['dataset_size']}",
        f"**評估 K**：{m['k']}",
        f"**整體結果**：{badge}",
        "",
        f"## 業務目標：Hit Rate ≥ {BUSINESS_GOAL_HIT_RATE}",
        "",
        f"**當前 Hit Rate**：`{m['hit_rate']}`  →  {hit_state} (gating metric)",
        "",
        "其餘指標僅供參考，不影響 pass/fail 判定。",
        "",
        "## 檢索準確度指標",
        "",
        "| 指標 | 分數 | 門檻 | 是否 gating | 狀態 |",
        "|------|------|------|-------------|------|",
        f"| **Hit Rate** | **{m['hit_rate']}** | **{THRESHOLDS['hit_rate']}** | ✅ 業務目標 | {state(m['hit_rate'], THRESHOLDS['hit_rate'])} |",
        f"| Precision@K | {m['precision_at_k']} | {THRESHOLDS['precision_at_k']} | info | {state(m['precision_at_k'], THRESHOLDS['precision_at_k'])} |",
        f"| Recall@K | {m['recall_at_k']} | {THRESHOLDS['recall_at_k']} | info | {state(m['recall_at_k'], THRESHOLDS['recall_at_k'])} |",
        f"| F1@K | {m['f1_at_k']} | {THRESHOLDS['f1_at_k']} | info | {state(m['f1_at_k'], THRESHOLDS['f1_at_k'])} |",
        f"| MRR | {m['mrr']} | {THRESHOLDS['mrr']} | info | {state(m['mrr'], THRESHOLDS['mrr'])} |",
        f"| MAP | {m['map']} | {THRESHOLDS['map']} | info | {state(m['map'], THRESHOLDS['map'])} |",
        f"| 評估筆數 | {m['evaluated']} | — | — |",
        f"| 跳過筆數（無 ground truth） | {m['skipped']} | — | — |",
        "",
        "---",
        "*由 `monitoring_addon/scripts/run_extended_vv.py` 自動產生。本腳本獨立於 RAG/scripts/run_vv_evaluation.py，主系統行為不受影響。*",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extended V&V (Recall@K + F1@K + MAP)")
    ap.add_argument("--golden", default=None, help="Golden dataset JSON")
    ap.add_argument("-k", "--top-k", type=int, default=5)
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "reports"),
    )
    args = ap.parse_args()

    golden_path = Path(args.golden) if args.golden else default_golden_path()
    dataset = load_golden_dataset(golden_path)
    print(f"Loaded {len(dataset)} entries from {golden_path}")

    metrics = evaluate(dataset, k=args.top_k)
    status = vv_status(metrics)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_size": len(dataset),
        "dataset_path": str(golden_path),
        "business_goal": {
            "metric": "hit_rate",
            "target": BUSINESS_GOAL_HIT_RATE,
            "current": metrics.get("hit_rate"),
            "met": metrics["evaluated"] > 0 and metrics.get("hit_rate", 0) >= BUSINESS_GOAL_HIT_RATE,
        },
        "thresholds": THRESHOLDS,
        "gating_metrics": sorted(GATING_METRICS),
        "metrics": metrics,
        "status": status,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"extended_vv_{date_str}.json"
    md_path = output_dir / f"extended_vv_{date_str}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"\nResult: {status}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
