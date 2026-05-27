"""Read-only loader for RAG/data/audit_logs/*.jsonl.

Never writes back to RAG/. All paths are resolved from RAG_DATA_DIR (default
"../RAG/data") and the addon may be relocated independently of RAG/.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional


def default_audit_dir() -> Path:
    base = os.environ.get("RAG_DATA_DIR", "../RAG/data")
    return Path(base) / "audit_logs"


def list_audit_files(audit_dir: Optional[Path] = None) -> List[Path]:
    """Return sorted .jsonl files in audit_dir (oldest first)."""
    d = audit_dir or default_audit_dir()
    if not d.exists():
        return []
    return sorted(d.glob("audit_*.jsonl"))


def filter_by_window(
    files: Iterable[Path],
    window_days: int,
    *,
    today: Optional[datetime] = None,
) -> List[Path]:
    """Keep only files whose date is within the last `window_days` from today.

    Filename pattern: audit_YYYY-MM-DD.jsonl
    """
    if window_days <= 0:
        return list(files)
    ref = today or datetime.now()
    cutoff = ref - timedelta(days=window_days)
    out: List[Path] = []
    for f in files:
        try:
            stem = f.stem.replace("audit_", "")
            day = datetime.strptime(stem, "%Y-%m-%d")
        except ValueError:
            continue
        if day >= cutoff:
            out.append(f)
    return out


def load_events(files: Iterable[Path]) -> List[dict]:
    """Read jsonl files and return all records as dicts (skip bad lines)."""
    events: List[dict] = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            continue
    return events


def partition_events(events: List[dict]) -> dict:
    """Group events by event_type for downstream consumers."""
    buckets: dict = {}
    for e in events:
        t = e.get("event_type", "unknown")
        buckets.setdefault(t, []).append(e)
    return buckets
