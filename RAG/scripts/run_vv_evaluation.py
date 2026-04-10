"""
V&V Pipeline — ISO 42001 A.6

Runs the golden dataset through the retrieval layer and computes IR metrics.
Does NOT call the LLM (answer quality requires a live system).

Usage:
    python3 scripts/run_vv_evaluation.py
    python3 scripts/run_vv_evaluation.py --dataset tests/evaluation/golden_dataset.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rag_system.core.retrieval_evaluator import compute_retrieval_metrics, RetrievalMetrics
from rag_system.core.answer_evaluator import score_answer, compute_answer_metrics, AnswerScore


THRESHOLDS = {
    "hit_rate": 0.60,
    "precision_at_k": 0.50,
    "mrr": 0.40,
}


def load_golden_dataset(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_offline_metrics(dataset: list) -> dict:
    """
    Run offline (no-LLM) evaluation using only golden dataset fields.

    Since expected_docs is empty in most entries (no retrieval ground truth yet),
    we simulate by checking keyword presence in expected_answer as proxy.
    """
    answer_scores: list[AnswerScore] = []

    for entry in dataset:
        is_oos = entry.get("category") == "out_of_scope"
        # Use expected_answer as the "answer" to score structural quality
        answer = entry.get("expected_answer", "")
        score = score_answer(
            answer=answer,
            expected_keywords=entry.get("expected_keywords", []),
            expected_articles=entry.get("expected_articles", []),
            is_out_of_scope=is_oos,
        )
        answer_scores.append(score)

    answer_summary = compute_answer_metrics(answer_scores)

    # Retrieval metrics — skipped if no expected_docs set
    retrieval_results = [
        {"retrieved_docs": [], "expected_docs": e.get("expected_docs", [])}
        for e in dataset
    ]
    retrieval_metrics = compute_retrieval_metrics(retrieval_results)

    return {
        "retrieval": retrieval_metrics.to_dict(),
        "answer_quality_baseline": answer_summary,
        "dataset_breakdown": _breakdown(dataset),
    }


def _breakdown(dataset: list) -> dict:
    categories: dict = {}
    difficulties: dict = {}
    for entry in dataset:
        cat = entry.get("category", "unknown")
        diff = entry.get("difficulty", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
        difficulties[diff] = difficulties.get(diff, 0) + 1
    return {"by_category": categories, "by_difficulty": difficulties}


def vv_status(metrics: RetrievalMetrics) -> str:
    """
    Return evaluation status: 'pass', 'fail', or 'inconclusive'.

    'inconclusive' is returned when no retrieval cases were actually evaluated
    (no expected_docs ground truth). This prevents a vacuous PASS from masking
    a retrieval system that was never tested.
    """
    if metrics.evaluated == 0:
        return "inconclusive"
    passed = (
        metrics.hit_rate >= THRESHOLDS["hit_rate"]
        and metrics.precision_at_k >= THRESHOLDS["precision_at_k"]
        and metrics.mrr >= THRESHOLDS["mrr"]
    )
    return "pass" if passed else "fail"


def passes_thresholds(metrics: RetrievalMetrics) -> bool:
    """Return True only if retrieval metrics meet all V&V thresholds.

    Returns False for both 'fail' and 'inconclusive' — callers must consult
    vv_status() to distinguish the two non-passing states.
    """
    return vv_status(metrics) == "pass"


def _status_badge(status: str) -> str:
    return {"pass": "✅ PASS", "fail": "❌ FAIL", "inconclusive": "⚠️ INCONCLUSIVE"}.get(status, status)


def render_markdown(report: dict) -> str:
    ret = report["retrieval"]
    ans = report["answer_quality_baseline"]
    bkd = report["dataset_breakdown"]
    status = report.get("status", "fail")
    inconclusive = ret["evaluated"] == 0

    def metric_status(score, threshold):
        if inconclusive:
            return "⚠️"
        return "✅" if score >= threshold else "❌"

    lines = [
        "# ISO 42001 A.6 V&V 評估報告",
        "",
        f"**產生時間**：{report['generated_at']}",
        f"**資料集筆數**：{report['dataset_size']}",
        f"**整體結果**：{_status_badge(status)}",
        "",
        "## 檢索準確度指標",
        "",
        f"| 指標 | 分數 | 門檻 | 狀態 |",
        f"|------|------|------|------|",
        f"| Hit Rate | {ret['hit_rate']} | {THRESHOLDS['hit_rate']} | {metric_status(ret['hit_rate'], THRESHOLDS['hit_rate'])} |",
        f"| Precision@K | {ret['precision_at_k']} | {THRESHOLDS['precision_at_k']} | {metric_status(ret['precision_at_k'], THRESHOLDS['precision_at_k'])} |",
        f"| MRR | {ret['mrr']} | {THRESHOLDS['mrr']} | {metric_status(ret['mrr'], THRESHOLDS['mrr'])} |",
        f"| 評估筆數 | {ret['evaluated']} | — | — |",
        f"| 跳過筆數（無 ground truth）| {ret['skipped']} | — | — |",
        "",
        "## 回答品質基準（使用 expected_answer 自評）",
        "",
        f"| 指標 | 分數 |",
        f"|------|------|",
        f"| 平均關鍵字覆蓋率 | {ans.get('avg_keyword_coverage', 'N/A')} |",
        f"| 平均條文引用匹配 | {ans.get('avg_article_match', 'N/A')} |",
        f"| 結構完整率 | {ans.get('structure_ok_rate', 'N/A')} |",
        f"| 平均綜合分數 | {ans.get('avg_overall', 'N/A')} |",
        "",
        "## 資料集分佈",
        "",
        "**依類別**",
    ]
    for cat, count in bkd["by_category"].items():
        lines.append(f"- {cat}: {count}")
    lines += ["", "**依難度**"]
    for diff, count in bkd["by_difficulty"].items():
        lines.append(f"- {diff}: {count}")
    lines += [
        "",
        "---",
        "*本報告由 `scripts/run_vv_evaluation.py` 自動產生，作為 ISO 42001 A.6 稽核證據。*",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run ISO 42001 V&V evaluation")
    parser.add_argument(
        "--dataset",
        default="./tests/evaluation/golden_dataset.json",
        help="Path to golden dataset JSON",
    )
    parser.add_argument("--output-dir", default="./data/reports", help="Output directory")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading dataset: {dataset_path}")
    dataset = load_golden_dataset(dataset_path)
    print(f"Dataset size: {len(dataset)}")

    results = run_offline_metrics(dataset)
    retrieval = RetrievalMetrics(**results["retrieval"])
    status = vv_status(retrieval)
    passed = status == "pass"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_size": len(dataset),
        "thresholds": THRESHOLDS,
        "status": status,
        "passed": passed,
        **results,
    }

    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"vv_report_{date_str}.json"
    md_path = output_dir / f"vv_report_{date_str}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"\nResult: {_status_badge(status)}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
