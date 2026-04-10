"""
Monitoring Report Generator — ISO 42001 A.6

Reads all audit log files from data/audit_logs/ and produces:
  - data/reports/monitoring_<date>.json  (machine-readable)
  - data/reports/monitoring_<date>.md    (human-readable / audit evidence)

Usage:
    python3 scripts/generate_monitoring_report.py
    python3 scripts/generate_monitoring_report.py --log-dir /custom/path
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag_system.core.anomaly_detector import analyse_log_file


def build_report(log_dir: Path) -> dict:
    """Scan all .jsonl files in log_dir and aggregate into a report."""
    log_files = sorted(log_dir.glob("audit_*.jsonl"))
    if not log_files:
        return {"error": f"No audit log files found in {log_dir}"}

    per_day = []
    totals = {
        "total_events": 0,
        "query_count": 0,
        "rejection_count": 0,
        "security_alert_count": 0,
        "anomalous_events": 0,
    }
    all_latencies: list[int] = []
    all_flags: dict = {}

    for log_file in log_files:
        day_stats = analyse_log_file(log_file)
        per_day.append(day_stats)

        for key in totals:
            totals[key] += day_stats.get(key, 0) or 0

        if day_stats.get("avg_latency_ms"):
            all_latencies.append(day_stats["avg_latency_ms"])

        for flag, count in (day_stats.get("anomaly_flags_summary") or {}).items():
            all_flags[flag] = all_flags.get(flag, 0) + count

    overall_rejection_rate = (
        round(totals["rejection_count"] / totals["query_count"], 3)
        if totals["query_count"] else None
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "log_directory": str(log_dir),
        "days_covered": len(log_files),
        "summary": {
            **totals,
            "overall_rejection_rate": overall_rejection_rate,
            "avg_daily_latency_ms": int(sum(all_latencies) / len(all_latencies)) if all_latencies else None,
            "anomaly_flags_total": all_flags,
        },
        "per_day": per_day,
    }


def render_markdown(report: dict) -> str:
    """Render the report dict as a Markdown document (audit evidence)."""
    s = report.get("summary", {})
    lines = [
        "# ISO 42001 A.6 監控報告",
        "",
        f"**產生時間**：{report.get('generated_at', 'N/A')}",
        f"**涵蓋天數**：{report.get('days_covered', 0)} 天",
        "",
        "## 彙總統計",
        "",
        f"| 指標 | 數值 |",
        f"|------|------|",
        f"| 總事件數 | {s.get('total_events', 0)} |",
        f"| 查詢次數 | {s.get('query_count', 0)} |",
        f"| 拒絕次數 | {s.get('rejection_count', 0)} |",
        f"| 拒絕率 | {s.get('overall_rejection_rate', 'N/A')} |",
        f"| 安全告警次數 | {s.get('security_alert_count', 0)} |",
        f"| 異常事件數 | {s.get('anomalous_events', 0)} |",
        f"| 平均日延遲 (ms) | {s.get('avg_daily_latency_ms', 'N/A')} |",
        "",
    ]

    flags = s.get("anomaly_flags_total", {})
    if flags:
        lines += [
            "## 異常旗標彙總",
            "",
            "| 旗標類型 | 次數 |",
            "|----------|------|",
        ]
        for flag, count in sorted(flags.items(), key=lambda x: -x[1]):
            lines.append(f"| `{flag}` | {count} |")
        lines.append("")

    lines += [
        "## 每日明細",
        "",
    ]
    for day in report.get("per_day", []):
        fname = Path(day.get("log_file", "unknown")).name
        lines += [
            f"### {fname}",
            f"- 查詢：{day.get('query_count', 0)}  拒絕：{day.get('rejection_count', 0)}  "
            f"安全告警：{day.get('security_alert_count', 0)}  異常：{day.get('anomalous_events', 0)}",
            f"- 平均延遲：{day.get('avg_latency_ms', 'N/A')} ms  "
            f"P95：{day.get('p95_latency_ms', 'N/A')} ms",
            f"- 拒絕率：{day.get('rejection_rate', 'N/A')}",
            "",
        ]

    lines += [
        "---",
        "*本報告由 `scripts/generate_monitoring_report.py` 自動產生，作為 ISO 42001 A.6 稽核證據。*",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate ISO 42001 monitoring report")
    parser.add_argument("--log-dir", default="./data/audit_logs", help="Audit log directory")
    parser.add_argument("--output-dir", default="./data/reports", help="Output directory")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning logs in: {log_dir}")
    report = build_report(log_dir)

    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"monitoring_{date_str}.json"
    md_path = output_dir / f"monitoring_{date_str}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"JSON report: {json_path}")
    print(f"Markdown report: {md_path}")

    summary = report.get("summary", {})
    print(f"\nSummary: {summary.get('query_count', 0)} queries, "
          f"{summary.get('rejection_count', 0)} rejections, "
          f"{summary.get('security_alert_count', 0)} security alerts, "
          f"{summary.get('anomalous_events', 0)} anomalies")


if __name__ == "__main__":
    main()
