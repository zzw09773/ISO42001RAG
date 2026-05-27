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
