"""Regression gate — per-query flip matrix between two online V&V reports.

Motivation (see docs/REGRESSION_GATE.md): the 2026-05-27 HyDE experiment
fixed eval_m03/cr02 and broke eval_m10/cr04 in the same change. Aggregate
Hit Rate stayed at 0.9355, the failed-set swap went unnoticed for 8
versions, and m10/cr04 are still the two production failures today.
Aggregate metrics cannot catch this; only a per-query flip matrix can.

Gate rules (conservative by design):
  - `newly_failed` empty                       → PASS
  - `newly_failed` cleared by UNANIMOUS rerun  → PASS  (all N reruns pass)
  - anything else (2:1 majority, no rerun, …)  → FAIL

A 2:1 rerun split does NOT clear a regression — ambiguity fails the gate
and is recorded in the per-query stability ledger. The single-run baseline
is likewise never treated as absolute truth: every flip observation is
written to the ledger so chronic flappers become visible across versions.

This module holds the pure comparison/verdict logic (unit-tested, no I/O
beyond the stability ledger helpers). API reruns live in
scripts/run_regression_gate.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

PASS, FAIL, SKIPPED = "pass", "fail", "skipped"


def query_status(record: dict) -> str:
    """Map one per_query record (run_online_vv.py shape) to pass/fail/skipped.

    - out_of_scope:        pass ⇔ is_rejection_correct is True
    - in-scope w/ golden:  pass ⇔ hit_rate > 0 (per-query hit rate is binary)
    - in-scope w/o golden: skipped (hit_rate is None — not comparable)
    """
    if record.get("category") == "out_of_scope":
        return PASS if record.get("is_rejection_correct") else FAIL
    hr = record.get("hit_rate")
    if hr is None:
        return SKIPPED
    return PASS if hr > 0 else FAIL


def compare_reports(baseline: dict, current: dict) -> dict:
    """Build the flip matrix between two V&V reports' per_query arrays.

    Only IDs present in both reports are compared; asymmetric IDs are
    reported separately (a silent dataset change must not look like a
    clean PASS).
    """
    base_q = {r["id"]: r for r in baseline.get("per_query", [])}
    cur_q = {r["id"]: r for r in current.get("per_query", [])}

    common = sorted(base_q.keys() & cur_q.keys())
    result = {
        "newly_failed": [],
        "newly_passed": [],
        "still_failed": [],
        "still_passed": [],
        "skipped": [],
        "only_in_baseline": sorted(base_q.keys() - cur_q.keys()),
        "only_in_current": sorted(cur_q.keys() - base_q.keys()),
        "details": {},
    }

    for qid in common:
        b, c = query_status(base_q[qid]), query_status(cur_q[qid])
        if SKIPPED in (b, c):
            bucket = "skipped"
        elif b == PASS and c == FAIL:
            bucket = "newly_failed"
        elif b == FAIL and c == PASS:
            bucket = "newly_passed"
        elif c == FAIL:
            bucket = "still_failed"
        else:
            bucket = "still_passed"
        result[bucket].append(qid)
        result["details"][qid] = {
            "baseline": b,
            "current": c,
            "query": cur_q[qid].get("query"),
            "category": cur_q[qid].get("category"),
            "expected_articles": cur_q[qid].get("expected_articles"),
            "baseline_cited": base_q[qid].get("cited_articles"),
            "current_cited": cur_q[qid].get("cited_articles"),
        }
    return result


def rerun_verdict(results: List[str]) -> dict:
    """Majority verdict over N rerun statuses for one flip query.

    Conservative contract: a suspected regression (newly_failed) is cleared
    ONLY by a unanimous pass. A 2:1 split in either direction is ambiguous
    → cleared=False, recorded as unstable.
    """
    n_pass = sum(1 for r in results if r == PASS)
    n_fail = len(results) - n_pass
    return {
        "results": list(results),
        "majority": PASS if n_pass > n_fail else FAIL,
        "unanimous": n_pass == len(results) or n_fail == len(results),
        "cleared": bool(results) and n_pass == len(results),
    }


def gate_verdict(comparison: dict, reruns: Optional[Dict[str, dict]]) -> dict:
    """Final gate decision.

    reruns: {query_id: rerun_verdict(...)} for newly_failed queries, or
    None when reruns were not executed (API unreachable / --no-rerun).
    Without rerun confirmation every newly_failed stays a suspected
    regression and the gate FAILs — "couldn't verify" is not a pass.
    """
    newly_failed = comparison["newly_failed"]
    reasons: List[str] = []

    if not newly_failed:
        verdict = "PASS"
        reasons.append("無 newly_failed —— 沒有任何基線通過的題目在本版失敗。")
    elif reruns is None:
        verdict = "FAIL"
        reasons.append(
            f"{len(newly_failed)} 題 newly_failed 且未執行重跑驗證"
            f"（{', '.join(newly_failed)}）——無法排除真回歸，保守判 FAIL。"
        )
    else:
        confirmed = [q for q in newly_failed if not reruns.get(q, {}).get("cleared")]
        cleared = [q for q in newly_failed if reruns.get(q, {}).get("cleared")]
        if cleared:
            reasons.append(f"重跑全數通過（噪音排除）：{', '.join(cleared)}")
        if confirmed:
            verdict = "FAIL"
            for q in confirmed:
                rv = reruns.get(q)
                detail = f"重跑結果 {rv['results']}" if rv else "無重跑紀錄"
                reasons.append(f"確認回歸或結果含糊：{q}（{detail}）——2:1 含糊一律保守 FAIL。")
        else:
            verdict = "PASS"
            reasons.append("所有 newly_failed 經重跑一致通過，判定為單次執行噪音。")

    if comparison["only_in_baseline"] or comparison["only_in_current"]:
        reasons.append(
            "⚠️ 兩份報告題目集不一致"
            f"（僅基線有: {comparison['only_in_baseline']}；僅本版有: {comparison['only_in_current']}）"
            "——比對僅及交集，資料集變更須另行 re-baseline。"
        )

    if comparison["newly_passed"]:
        reasons.append(
            f"newly_passed: {', '.join(comparison['newly_passed'])}"
            "（改善宣稱同樣受單次噪音影響，建議重跑確認後才寫入 CHANGELOG）。"
        )

    return {"verdict": verdict, "reasons": reasons}


# ── Per-query stability ledger ──────────────────────────────────────────
# The baseline is a single run and is never absolute truth; every flip
# observation accumulates here so chronic flappers are visible across
# versions (verifier condition #1 on proposal AG-3).

def load_stability_records(path: Path) -> dict:
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def record_flip_observation(
    records: dict,
    qid: str,
    *,
    date: str,
    tag: str,
    direction: str,
    rerun: Optional[dict] = None,
) -> None:
    """Append one flip observation for query `qid` (mutates `records`)."""
    entry = records.setdefault(qid, {"observations": [], "unstable": False})
    obs = {
        "date": date,
        "tag": tag,
        "direction": direction,  # newly_failed | newly_passed
        "kind": "flip_rerun" if rerun else "flip_unverified",
    }
    if rerun:
        obs["results"] = rerun["results"]
        obs["majority"] = rerun["majority"]
        obs["unanimous"] = rerun["unanimous"]
        if not rerun["unanimous"]:
            entry["unstable"] = True
    entry["observations"].append(obs)
    # Two or more flip observations across versions ⇒ chronic flapper.
    if len(entry["observations"]) >= 2:
        entry["unstable"] = True


def save_stability_records(records: dict, path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
