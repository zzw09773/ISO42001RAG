#!/usr/bin/env python3
"""
Run drift detection — Performance / Data / Embedding.

Reads from RAG main system (audit_logs + golden dataset + latest V&V report)
and writes drift_YYYY-MM-DD.{json,md} to monitoring_addon/data/reports/.

Usage:
    python3 scripts/run_drift_detection.py
    python3 scripts/run_drift_detection.py --window-days 14
    python3 scripts/run_drift_detection.py \
        --audit-dir   ../RAG/data/audit_logs \
        --golden      ../RAG/tests/evaluation/golden_dataset.json \
        --baseline    ../RAG/data/reports/vv_report_2026-04-09.json
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.audit_loader import filter_by_window, list_audit_files, load_events
from monitoring.baseline_loader import (
    extract_baseline_queries,
    load_golden_dataset,
    load_vv_report,
)
from monitoring.drift_detector import build_drift_report


SEVERITY_BADGE = {
    "normal": "✅ NORMAL",
    "warning": "⚠️  WARNING",
    "critical": "❌ CRITICAL",
}


def render_markdown(report_dict: dict) -> str:
    perf = report_dict["perf"]
    data = report_dict["data"]
    emb = report_dict["embedding"]
    sev = report_dict.get("severity", "normal")
    reasons = report_dict.get("severity_reasons", [])

    lines = [
        "# ISO 42001 A.6 漂移監測報告",
        "",
        f"**產生時間**：{report_dict['generated_at']}",
        f"**觀察視窗**：近 {report_dict['window_days']} 天",
        f"**視窗內查詢數**：{report_dict['queries_in_window']}",
        f"**基線**：`{report_dict['baseline_label']}`",
        f"**整體嚴重度**：{SEVERITY_BADGE.get(sev, sev)}",
        "",
        "### 嚴重度判定依據",
    ]
    for r in reasons:
        lines.append(f"- {r}")
    lines += [
        "",
        "## 1. 效能漂移 (Performance Drift)",
        "",
        "| 指標 | 基線 | 當期 | 變動 |",
        "|------|------|------|------|",
        f"| 拒絕率 | {perf['rejection_rate_baseline']} | {perf['rejection_rate_current']} | {perf['rejection_rate_delta']:+.4f} |",
        f"| 引用率 | {perf['citation_rate_baseline']} | {perf['citation_rate_current']} | {perf['citation_rate_delta']:+.4f} |",
        f"| 平均延遲 (ms) | {perf['avg_latency_baseline_ms']} | {perf['avg_latency_current_ms']} | "
        f"{(str(perf['avg_latency_delta_pct']) + ' (pct)') if perf['avg_latency_delta_pct'] is not None else 'N/A'} |",
        f"| P95 延遲 (ms) | — | {perf['p95_latency_current_ms']} | — |",
        f"| 安全告警率 | — | {perf['security_alert_rate_current']} | — |",
        f"| 重試率 | — | {perf['retry_rate_current']} | — |",
        f"| 觀察查詢數 | — | {perf['queries_observed']} | — |",
        "",
        "## 2. 資料漂移 (Data Drift)",
        "",
        "| 指標 | 數值 | 解讀（PSI） |",
        "|------|------|-------------|",
        f"| 查詢長度 PSI | {data['query_length_psi']} | <0.10 穩定／0.10–0.25 微漂／>0.25 嚴重 |",
        f"| 條文號頻率 PSI | {data['article_freq_psi']} | 同上 |",
        f"| 字元 unigram KL | {data['char_unigram_kl']} | KL 散度；越大越異常 |",
        f"| 觀察查詢數 | {data['queries_observed']} | — |",
        "",
        "**Top-10 條文頻率變動**（current proportion − baseline proportion）：",
        "",
    ]
    for art, delta in data.get("top_drift_articles", []):
        lines.append(f"- `{art}`： {delta:+.4f}")

    lines += [
        "",
        "## 3. 語意（Embedding）漂移",
        "",
        f"- 後端：`{emb['backend']}`（embeddings = 真實向量；char_ngram = 退化模式）",
        f"- 樣本數：{emb['samples']}",
        f"- 中心點 cosine 距離：{emb['centroid_cosine_distance']}",
        f"- PC1 投影 PSI：{emb['pca_first_component_psi']}",
        "",
        "---",
        "*由 `monitoring_addon/scripts/run_drift_detection.py` 自動產生，作為 ISO 42001 A.6.2.4 證據。*",
        "*嚴重度判定來自 `monitoring/thresholds.py`，門檻由稽核負責人配置。*",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run ISO 42001 drift detection")
    ap.add_argument("--audit-dir", default=None, help="RAG audit log directory")
    ap.add_argument("--golden", default=None, help="Golden dataset JSON path")
    ap.add_argument("--baseline", default=None, help="Optional V&V report JSON")
    ap.add_argument("--window-days", type=int, default=7, help="Days of audit logs to analyse")
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "reports"),
        help="Where to write drift_*.json/.md",
    )
    args = ap.parse_args()

    audit_dir = Path(args.audit_dir) if args.audit_dir else None
    golden_path = Path(args.golden) if args.golden else None
    vv_path = Path(args.baseline) if args.baseline else None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = list_audit_files(audit_dir)
    files_w = filter_by_window(files, args.window_days)
    print(f"Loaded {len(files_w)} of {len(files)} audit files within window")
    events = load_events(files_w)
    print(f"Total events: {len(events)}")

    golden = load_golden_dataset(golden_path)
    base_queries = extract_baseline_queries(golden)
    vv = load_vv_report(vv_path)
    print(f"Baseline queries: {len(base_queries)}  | V&V report keys: {list(vv.keys())[:5]}")

    report = build_drift_report(
        events,
        baseline_vv_report=vv,
        baseline_queries=base_queries,
        window_days=args.window_days,
        baseline_label=str(golden_path or "default_golden"),
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"drift_{date_str}.json"
    md_path = output_dir / f"drift_{date_str}.md"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report.to_dict()), encoding="utf-8")

    print(f"\nJSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"\nResult: {SEVERITY_BADGE.get(report.severity, report.severity)}")
    return 0 if report.severity == "normal" else 1


if __name__ == "__main__":
    sys.exit(main())
