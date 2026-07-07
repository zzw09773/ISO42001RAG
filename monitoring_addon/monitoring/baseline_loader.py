"""Load baseline data (golden dataset + V&V report) used as the
fixed reference point for drift comparison.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional


def default_golden_path() -> Path:
    """Resolve the golden dataset path.

    Lookup order (first existing wins):
      1. $GOLDEN_DATASET env var (explicit override)
      2. monitoring_addon/data/golden_dataset.json (addon-owned, 33 entries)
      3. ../RAG/tests/evaluation/golden_dataset.json (legacy, 30 entries)

    The addon-owned dataset is preferred because it carries the
    `expected_docs` / `expected_articles` fields required by Hit Rate
    computation; the legacy RAG/-owned dataset is kept as fallback only.
    """
    env_path = os.environ.get("GOLDEN_DATASET")
    if env_path:
        return Path(env_path)

    addon_path = Path(__file__).resolve().parent.parent / "data" / "golden_dataset.json"
    if addon_path.exists():
        return addon_path

    return Path("../RAG/tests/evaluation/golden_dataset.json")


def load_golden_dataset(path: Optional[Path] = None) -> List[dict]:
    p = path or default_golden_path()
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return []


def extract_baseline_queries(golden: List[dict]) -> List[str]:
    """Pull just the query text from the golden dataset — used as the
    baseline distribution for data drift and embedding drift.
    """
    out: List[str] = []
    for entry in golden:
        q = entry.get("query") or entry.get("question")
        if isinstance(q, str) and q.strip():
            out.append(q)
    return out


def load_vv_report(path: Optional[Path] = None) -> dict:
    """Load latest V&V report (used for baseline metric values).

    Search order when `path` is None (latest file wins, addon takes priority):
      1. monitoring_addon/data/reports/vv_report_*.json  (online V&V output)
      2. ../RAG/data/reports/vv_report_*.json            (RAG main system V&V)

    Returns empty dict if none available — drift detection will then use
    sensible defaults.
    """
    if path is None:
        addon_reports = Path(__file__).resolve().parent.parent / "data" / "reports"
        rag_reports = Path(os.environ.get("RAG_DATA_DIR", "../RAG/data")) / "reports"

        candidates = sorted(addon_reports.glob("vv_report_*.json"))
        if not candidates:
            candidates = sorted(rag_reports.glob("vv_report_*.json"))
        if not candidates:
            return {}
        path = candidates[-1]
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_ragas_report(path: Optional[Path] = None) -> dict:
    """Load the latest RAGAS report (ragas_*.json) — answer-grounding metrics.

    Produced by scripts/run_ragas_evaluation.py. Used to populate the drift
    report's faithfulness field so the dashboard shows a real number instead
    of "未啟用". Returns {} if no usable report exists yet (fresh deploy).

    Schema gate (P5): only accept reports carrying the current `judge_prompt`
    marker. Pre-fix reports (which could hold a fake 0.0 from an evaluator
    outage, or 1.0 from a refusal) lack it and are REJECTED, so a stale legacy
    report never shows a misleading number — the dashboard falls back to
    "尚未評估".
    """
    if path is None:
        reports = Path(__file__).resolve().parent.parent / "data" / "reports"
        candidates = sorted(reports.glob("ragas_*.json"))
        if not candidates:
            return {}
        path = candidates[-1]
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(report, dict) or not report.get("judge_prompt"):
        return {}                      # legacy / unknown schema → reject
    return report


def ragas_report_meta(report: dict) -> dict:
    """Freshness / provenance meta for the dashboard (P5).

    Returns {available, generated_at, judge_model, age_days, stale,
    freshness_days}. `stale` is True when the report is older than
    config.RAGAS_FRESHNESS_DAYS — faithfulness is a point-in-time snapshot, so
    its age must be surfaced, not presented as a live value.
    """
    from datetime import datetime, timezone
    from .config import RAGAS_FRESHNESS_DAYS
    if not report:
        return {"available": False, "freshness_days": RAGAS_FRESHNESS_DAYS}
    gen = report.get("generated_at")
    age_days = None
    stale = False
    if gen:
        try:
            dt = datetime.fromisoformat(str(gen).replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - dt).days
            stale = age_days > RAGAS_FRESHNESS_DAYS
        except Exception:
            stale = True            # unparseable timestamp → flag stale, never "fresh"
    else:
        stale = True                # no timestamp → unknown freshness, flag it
    return {
        "available": True,
        "generated_at": gen,
        "judge_model": report.get("judge_model"),
        "age_days": age_days,
        "stale": stale,
        "freshness_days": RAGAS_FRESHNESS_DAYS,
    }
