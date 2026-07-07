#!/usr/bin/env python3
"""
Run service-health check — rejection / latency / faithfulness / availability /
security / audit-chain integrity.

Reads from the RAG main system (audit_logs + latest V&V report + latest RAGAS
report) plus the addon's own availability_log.jsonl / integrity_state.json, and
writes health_YYYY-MM-DD.{json,md} to monitoring_addon/data/reports/.

Distribution / embedding drift (PSI / JSD) was intentionally removed — see
docs/superpowers/specs/2026-06-26-monitoring-slimdown-design.md.

Usage:
    python3 scripts/run_health_check.py
    python3 scripts/run_health_check.py --window-days 14
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.audit_loader import filter_by_window, list_audit_files, load_events
from monitoring.baseline_loader import load_ragas_report, load_vv_report
from monitoring.availability import load_availability
from monitoring.alert_checkers import load_integrity_state
from monitoring.health_monitor import build_health_report


SEVERITY_BADGE = {
    "normal": "✅ NORMAL",
    "watch": "🔵 WATCH",
    "warning": "⚠️  WARNING",
    "critical": "❌ CRITICAL",
    "insufficient_data": "➖ INSUFFICIENT DATA",
}


def render_markdown(d: dict) -> str:
    perf = d["perf"]
    avail = d.get("availability") or {}
    faith = d.get("faithfulness", {}) or {}
    sev = d.get("severity", "normal")
    lines = [
        "# ISO 42001 A.6.2.4 服務健康報告",
        "",
        f"**產生時間**：{d['generated_at']}",
        f"**觀察視窗**：近 {d['window_days']} 天",
        f"**視窗內查詢數**：{d['queries_in_window']}",
        f"**整體嚴重度**：{SEVERITY_BADGE.get(sev, sev)}（健康分數 **{d.get('overall_score', 0):.0f}/100**）",
        "",
        "### 分級制度",
        "| score | 級別 | 含意 |",
        "|------|------|------|",
        "| 0–25 | 🟢 normal | 正常波動 |",
        "| 25–50 | 🔵 watch | 輕微劣化，記錄留意 |",
        "| 50–75 | 🟡 warning | 明顯劣化，人工調查 |",
        "| 75–100 | 🔴 critical | 嚴重劣化／幻覺／服務中斷，立即處理 |",
        "",
        "### 各維度分數",
        "| 維度 | 分數 |",
        "|------|------|",
    ]
    for k, v in (d.get("dimension_scores") or {}).items():
        lines.append(f"| `{k}` | {v} |")
    lines += ["", "### 嚴重度判定依據"]
    for r in d.get("severity_reasons", []):
        lines.append(f"- {r}")
    lines += [
        "",
        "## 1. 運作健康 (Operational)",
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
        "",
        "## 2. 系統可用率 (Availability — 複合依賴探測)",
        "",
        f"- uptime：{avail.get('uptime_pct')}%（視窗內探測 {avail.get('probes', 0)} 次）",
        f"- 各依賴 uptime：{avail.get('per_dep_uptime', {})}",
        f"- hard-down（連續探測失敗）：{avail.get('hard_down', False)}",
        f"- 最後成功時間：{avail.get('last_ok_at')}",
        "",
        "## 3. audit 鏈完整性",
        "",
        f"- 最新狀態：`{d.get('last_integrity_status', 'unknown')}`（broken → 立即 critical）",
        "",
        "## 4. 幻覺 (Faithfulness) — 最關鍵維度",
        "",
    ]
    fc = faith.get("current")
    if fc is None:
        lines += ["- ⚠️ 本次未測量 faithfulness（跑 run_ragas_evaluation.py 後納入）。"]
    else:
        lines += [
            f"- **當期 Faithfulness**：{fc}（絕對門檻 {faith.get('target', 0.90)}）",
            f"- 維度分數：{(d.get('dimension_scores') or {}).get('faithfulness', 'N/A')}",
        ]
    lines += [
        "",
        "---",
        "*由 `monitoring_addon/scripts/run_health_check.py` 自動產生，作為 ISO 42001 A.6.2.4 證據。*",
        "*指標定義詳見 `monitoring_addon/docs/HEALTH_METRICS.md`。*",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run ISO 42001 service-health check")
    ap.add_argument("--audit-dir", default=None, help="RAG audit log directory")
    ap.add_argument("--golden", default=None, help="Golden dataset JSON path (label only)")
    ap.add_argument("--baseline", default=None, help="Optional V&V report JSON (perf baselines)")
    ap.add_argument("--window-days", type=int, default=7, help="Days of audit logs to analyse")
    ap.add_argument("--ragas-report", default=None,
                    help="Optional RAGAS report JSON — feeds faithfulness (hallucination)")
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "reports"),
        help="Where to write health_*.json/.md",
    )
    args = ap.parse_args()

    audit_dir = Path(args.audit_dir) if args.audit_dir else None
    vv_path = Path(args.baseline) if args.baseline else None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir.parent

    files = list_audit_files(audit_dir)
    files_w = filter_by_window(files, args.window_days)
    print(f"Loaded {len(files_w)} of {len(files)} audit files within window")
    events = load_events(files_w)
    print(f"Total events: {len(events)}")

    vv = load_vv_report(vv_path)

    faithfulness_current = None
    if args.ragas_report and Path(args.ragas_report).exists():
        try:
            rg = load_ragas_report(Path(args.ragas_report))
            faithfulness_current = (rg.get("aggregate") or {}).get("faithfulness")
        except Exception as e:
            print(f"Could not read RAGAS report: {e}")
    else:
        faithfulness_current = (load_ragas_report().get("aggregate") or {}).get("faithfulness")

    availability = load_availability(data_dir / "availability_log.jsonl")
    integrity_status = load_integrity_state(data_dir)
    print(f"Availability uptime: {availability.get('uptime_pct')}%  | integrity: {integrity_status}")

    report = build_health_report(
        events,
        baseline_vv_report=vv,
        window_days=args.window_days,
        baseline_label=str(Path(args.golden) if args.golden else "default_golden"),
        faithfulness_current=faithfulness_current,
        availability=availability,
        integrity_status=integrity_status,
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"health_{date_str}.json"
    md_path = output_dir / f"health_{date_str}.md"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report.to_dict()), encoding="utf-8")

    print(f"\nJSON: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"\nResult: {SEVERITY_BADGE.get(report.severity, report.severity)}")
    return 0 if report.severity in ("normal", "insufficient_data") else 1


if __name__ == "__main__":
    sys.exit(main())
