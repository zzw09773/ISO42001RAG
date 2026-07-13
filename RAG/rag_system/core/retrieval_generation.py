"""Cross-process generation marker for cached retrieval workflows."""
from __future__ import annotations

import os
from pathlib import Path
import uuid


def _marker_path() -> Path:
    configured = os.environ.get("RAG_RETRIEVAL_GENERATION_FILE")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "processed" / "retrieval-generation"


def current_retrieval_generation() -> str:
    try:
        return _marker_path().read_text(encoding="utf-8").strip() or "0"
    except FileNotFoundError:
        return "0"


def bump_retrieval_generation() -> str:
    """Atomically publish a new generation visible to every RAG process."""
    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    generation = uuid.uuid4().hex
    temporary = marker.with_name(f".{marker.name}.{generation}.tmp")
    temporary.write_text(generation + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, marker)
    marker.chmod(0o600)
    return generation
