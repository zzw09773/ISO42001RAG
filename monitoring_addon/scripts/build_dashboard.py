#!/usr/bin/env python3
"""
Build a static HTML monitoring dashboard.

Reads from RAG/data/audit_logs/, RAG/tests/evaluation/golden_dataset.json,
and the latest vv_report_*.json. Writes a fully self-contained HTML file
(inline SVG charts, no external dependencies) to
monitoring_addon/data/reports/dashboard_YYYY-MM-DD.html.

Usage:
    python3 scripts/build_dashboard.py
    python3 scripts/build_dashboard.py --window-days 14
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitoring.dashboard_data import build_payload, write_payload_json
from monitoring.dashboard_render import render_dashboard


def main() -> int:
    ap = argparse.ArgumentParser(description="Build static monitoring dashboard")
    ap.add_argument("--audit-dir", default=None)
    ap.add_argument("--golden", default=None)
    ap.add_argument("--vv-report", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "reports"),
    )
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = build_payload(
        audit_dir=Path(args.audit_dir) if args.audit_dir else None,
        golden_path=Path(args.golden) if args.golden else None,
        vv_report_path=Path(args.vv_report) if args.vv_report else None,
        window_days=args.window_days,
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    json_path = output_dir / f"dashboard_{date_str}.json"
    html_path = output_dir / f"dashboard_{date_str}.html"

    write_payload_json(payload, json_path)
    html_path.write_text(render_dashboard(payload), encoding="utf-8")

    print(f"JSON payload: {json_path}")
    print(f"HTML dashboard: {html_path}")
    print(f"Drift severity: {payload['drift']['severity']}")
    print(f"Open: file://{html_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
