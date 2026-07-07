#!/usr/bin/env python3
"""
Regression Gate — per-query flip 矩陣比對 + flip 題自動重跑多數決。

ISO 42001 A.6 證據工具。比較兩份 online V&V 報告（run_online_vv.py 產出，
含 per_query），輸出 newly_failed / newly_passed 矩陣：

  - newly_failed 非空 → gate FAIL（即使 aggregate Hit Rate 上升也擋——
    2026-05-27 HyDE 實驗的失敗集對調就是被平均數掩蓋了 8 個版本）。
  - newly_failed 預設自動重跑 N 次（--runs，預設 3）取多數決：
    全數通過才視為單次噪音放行；2:1 含糊一律保守 FAIL。
  - 每次 flip 觀察寫入 data/stability_records.json 穩定性帳本，
    跨版本累積，慣性翻轉題（flapper）會被標記 unstable。

程序性控制（非技術強制）：prompt / 檢索設定變更未過 gate 不得 bump 版本；
須越過 gate 時以 --override-reason/--override-operator 留下 A.6 紀錄。
流程見 RAG/docs/PROMPT_VERSIONS.md「變更流程」段與
monitoring_addon/docs/REGRESSION_GATE.md。

本腳本唯讀 RAG/（僅 importlib 載入 prompts.py 取 prompt_version_hash），
不修改主系統任何檔案。

Usage:
    python3 scripts/run_regression_gate.py \
        --baseline data/reports/online_vv_2026-05-27_v1.2-strict-verify.json \
        --current  data/reports/online_vv_<new>.json \
        --tag v1.2-promptfix
    # 後端不可達時只做比對不重跑（newly_failed 將保守判 FAIL）：
    python3 scripts/run_regression_gate.py --baseline ... --current ... --no-rerun

Exit codes: 0 = PASS（或 FAIL 但已留 override 紀錄）, 1 = FAIL, 2 = 用法/資料錯誤。
"""
import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ADDON_DIR = SCRIPTS_DIR.parent
sys.path.insert(0, str(ADDON_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from monitoring.regression_gate import (  # noqa: E402
    compare_reports,
    gate_verdict,
    load_stability_records,
    record_flip_observation,
    rerun_verdict,
    save_stability_records,
)

_TPE_TZ = timezone(timedelta(hours=8))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_prompt_version_hash() -> str | None:
    """Read-only load of RAG prompts.py by file path (no package import,
    no side effects — prompts.py only imports hashlib/json)."""
    prompts_py = ADDON_DIR.parent / "RAG" / "rag_system" / "core" / "prompts.py"
    try:
        spec = importlib.util.spec_from_file_location("_ro_prompts", prompts_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.prompt_version_hash()
    except Exception as e:
        print(f"WARN: 無法取得 prompt_version_hash：{e}", file=sys.stderr)
        return None


def _rerun_flips(comparison: dict, args) -> dict:
    """Re-ask the RAG API for each newly_failed query, N times each."""
    from run_online_vv import ask_rag, evaluate_one  # noqa: E402 (lazy: needs requests)
    from monitoring.regression_gate import query_status  # noqa: E402

    reruns = {}
    for qid in comparison["newly_failed"]:
        detail = comparison["details"][qid]
        entry = {
            "id": qid,
            "query": detail["query"],
            "category": detail["category"],
            "expected_articles": detail["expected_articles"] or [],
        }
        statuses = []
        for i in range(args.runs):
            answer = ask_rag(args.rag_url, entry["query"],
                             api_key=args.api_key, timeout=args.timeout)
            if not answer:
                print(f"  {qid} run {i+1}/{args.runs}: API 無回應，記為 fail", file=sys.stderr)
                statuses.append("fail")
            else:
                statuses.append(query_status(evaluate_one(entry, answer)))
            time.sleep(args.sleep_ms / 1000.0)
        reruns[qid] = rerun_verdict(statuses)
        print(f"  {qid}: 重跑 {statuses} → "
              f"{'噪音排除' if reruns[qid]['cleared'] else '回歸確認/含糊'}")
    return reruns


def render_markdown(report: dict) -> str:
    c = report["comparison"]
    lines = [
        "# Regression Gate 報告",
        "",
        f"**判定**：{'✅' if report['verdict'] == 'PASS' else '❌'} **{report['verdict']}**"
        + ("（已 override，見下）" if report.get("override") else ""),
        f"**產生時間**：{report['generated_at']}　**標籤**：{report['tag']}",
        f"**prompt_version_hash**：`{report['prompt_version_hash']}`",
        f"**基線**：`{report['baseline_path']}`（sha256 `{report['baseline_sha256'][:16]}…`）",
        f"**本版**：`{report['current_path']}`（sha256 `{report['current_sha256'][:16]}…`）",
        "",
        "| 桶 | 題數 | 題目 |",
        "|---|---|---|",
    ]
    for bucket in ("newly_failed", "newly_passed", "still_failed", "still_passed", "skipped"):
        ids = c[bucket]
        shown = ", ".join(ids) if bucket != "still_passed" else f"({len(ids)} 題)"
        lines.append(f"| {bucket} | {len(ids)} | {shown} |")
    lines += ["", "## 判定理由", ""]
    lines += [f"- {r}" for r in report["reasons"]]
    if report.get("reruns"):
        lines += ["", "## Flip 題重跑明細", ""]
        for qid, rv in report["reruns"].items():
            lines.append(f"- `{qid}`: {rv['results']} → majority={rv['majority']}, "
                         f"unanimous={rv['unanimous']}, cleared={rv['cleared']}")
    if report.get("override"):
        o = report["override"]
        lines += ["", "## Override 紀錄（A.6）", "",
                  f"- 操作者：{o['operator']}", f"- 理由：{o['reason']}",
                  f"- 時間：{o['timestamp']}"]
    lines += ["", "---", "*由 monitoring_addon/scripts/run_regression_gate.py 產生；"
              "本工具唯讀 RAG/，不修改主系統。*", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-query regression gate between two V&V reports")
    ap.add_argument("--baseline", required=True, help="基線 V&V 報告 JSON（含 per_query）")
    ap.add_argument("--current", required=True, help="本版 V&V 報告 JSON（含 per_query）")
    ap.add_argument("--tag", default="untagged", help="版本標籤（如 v1.2-promptfix）")
    ap.add_argument("--runs", type=int, default=3, help="flip 題重跑次數（預設 3）")
    ap.add_argument("--no-rerun", action="store_true", help="只比對不重跑（newly_failed 保守判 FAIL）")
    ap.add_argument("--rag-url", default="http://localhost:8043")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--sleep-ms", type=int, default=200)
    ap.add_argument("--output-dir", default=str(ADDON_DIR / "data" / "reports"))
    ap.add_argument("--stability-ledger", default=str(ADDON_DIR / "data" / "stability_records.json"))
    ap.add_argument("--override-reason", default=None,
                    help="越過 FAIL 的書面歸因（須同時給 --override-operator）")
    ap.add_argument("--override-operator", default=None, help="override 操作者姓名")
    args = ap.parse_args()

    if bool(args.override_reason) != bool(args.override_operator):
        print("ERROR: --override-reason 與 --override-operator 必須成對提供", file=sys.stderr)
        return 2

    try:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: 讀取報告失敗：{e}", file=sys.stderr)
        return 2
    if not baseline.get("per_query") or not current.get("per_query"):
        print("ERROR: 報告缺 per_query 欄位（須為 run_online_vv.py 產出格式）", file=sys.stderr)
        return 2

    comparison = compare_reports(baseline, current)
    if not comparison["details"]:
        print("ERROR: 兩份報告無共同題目，無法比對", file=sys.stderr)
        return 2

    reruns = None
    if comparison["newly_failed"] and not args.no_rerun:
        print(f"偵測到 {len(comparison['newly_failed'])} 題 newly_failed，"
              f"各重跑 {args.runs} 次（{args.rag_url}）…")
        reruns = _rerun_flips(comparison, args)

    decision = gate_verdict(comparison, reruns)

    now = datetime.now(_TPE_TZ)
    date_str = now.strftime("%Y-%m-%d")
    report = {
        "generated_at": now.isoformat(),
        "tag": args.tag,
        "verdict": decision["verdict"],
        "reasons": decision["reasons"],
        "prompt_version_hash": _load_prompt_version_hash(),
        "baseline_path": str(args.baseline),
        "baseline_sha256": _sha256_file(Path(args.baseline)),
        "current_path": str(args.current),
        "current_sha256": _sha256_file(Path(args.current)),
        "runs_per_flip": None if args.no_rerun else args.runs,
        "comparison": comparison,
        "reruns": reruns,
        "override": None,
    }
    if decision["verdict"] == "FAIL" and args.override_reason:
        report["override"] = {
            "operator": args.override_operator,
            "reason": args.override_reason,
            "timestamp": now.isoformat(),
        }

    # 穩定性帳本：每個 flip 觀察都入帳（含未重跑的，標記 unverified）
    ledger_path = Path(args.stability_ledger)
    ledger = load_stability_records(ledger_path)
    for qid in comparison["newly_failed"]:
        record_flip_observation(ledger, qid, date=date_str, tag=args.tag,
                                direction="newly_failed",
                                rerun=(reruns or {}).get(qid))
    for qid in comparison["newly_passed"]:
        record_flip_observation(ledger, qid, date=date_str, tag=args.tag,
                                direction="newly_passed")
    save_stability_records(ledger, ledger_path)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"regression_gate_{date_str}_{args.tag}.json"
    md_path = out_dir / f"regression_gate_{date_str}_{args.tag}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    print(f"\nGate 判定：{report['verdict']}"
          + ("（已留 override 紀錄）" if report["override"] else ""))
    for r in decision["reasons"]:
        print(f"  - {r}")
    print(f"\nJSON: {json_path}\nMarkdown: {md_path}\n穩定性帳本: {ledger_path}")

    if report["verdict"] == "PASS" or report["override"]:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
