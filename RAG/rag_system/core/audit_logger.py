"""
ISO 42001 Structured Audit Logger

Provides dual-output logging (local file + console) in JSON Lines format.
Covers ISO/IEC 42001 Annex A requirements:
  - A.4 Resource management (model name, token usage)
  - A.6 Lifecycle monitoring (response time, event types)
  - A.7 Data provenance (retrieved document sources)
  - A.9 Intended use & human oversight (scope check, user queries)
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Structured audit logger for ISO 42001 compliance.
    Writes JSON Lines to a daily-rotated file and echoes to console.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Console logger (structured output)
        self._console_logger = logging.getLogger("audit")
        if not self._console_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "[AUDIT] %(asctime)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            self._console_logger.addHandler(handler)
            self._console_logger.setLevel(logging.INFO)
            self._console_logger.propagate = False

    def _get_log_file(self) -> Path:
        """Get today's log file path (daily rotation)."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / f"audit_{today}.jsonl"

    def _write(self, record: Dict[str, Any]) -> None:
        """Write a single audit record to file and console."""
        # Add timestamp
        record["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Write to file (JSON Lines)
        log_file = self._get_log_file()
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

        # Echo to console
        event_type = record.get("event_type", "unknown")
        summary = self._format_console(record)
        self._console_logger.info(f"{event_type} | {summary}")

    def _format_console(self, record: Dict[str, Any]) -> str:
        """Format a record for concise console output."""
        parts = []
        if record.get("session_id"):
            parts.append(f"session={record['session_id'][:8]}")
        if record.get("user_query"):
            q = record["user_query"]
            parts.append(f"query=\"{q[:60]}{'...' if len(q) > 60 else ''}\"")
        if record.get("scope_check"):
            parts.append(f"scope={record['scope_check']}")
        if record.get("retrieved_docs"):
            parts.append(f"docs={len(record['retrieved_docs'])}")
        if record.get("tokens_used"):
            parts.append(f"tokens={record['tokens_used']}")
        if record.get("response_time_ms"):
            parts.append(f"time={record['response_time_ms']}ms")
        if record.get("model_name"):
            parts.append(f"model={record['model_name']}")
        if record.get("message"):
            parts.append(record["message"])
        return " | ".join(parts) if parts else json.dumps(record, ensure_ascii=False)

    # ---- High-level event methods ----

    def log_query(
        self,
        session_id: str,
        user_query: str,
        scope_check: str,
        model_name: str,
        retrieved_docs: Optional[List[str]] = None,
        tokens_used: Optional[int] = None,
        response_time_ms: Optional[int] = None,
    ) -> None:
        """Log a user query event."""
        self._write({
            "event_type": "query",
            "session_id": session_id,
            "user_query": user_query,
            "scope_check": scope_check,
            "model_name": model_name,
            "retrieved_docs": retrieved_docs or [],
            "tokens_used": tokens_used,
            "response_time_ms": response_time_ms,
        })

    def log_rejection(
        self,
        session_id: str,
        user_query: str,
        reason: str = "out_of_scope",
    ) -> None:
        """Log a rejected query (out of scope / ISO 42001 A.9)."""
        self._write({
            "event_type": "rejection",
            "session_id": session_id,
            "user_query": user_query,
            "scope_check": "out_of_scope",
            "reason": reason,
        })

    def log_upload(
        self,
        filename: str,
        indexed: bool,
        message: str,
    ) -> None:
        """Log a file upload/indexing event."""
        self._write({
            "event_type": "upload",
            "filename": filename,
            "indexed": indexed,
            "message": message,
        })

    def log_reindex(
        self,
        success: int,
        failed: int,
    ) -> None:
        """Log a reindex operation."""
        self._write({
            "event_type": "reindex",
            "success_count": success,
            "failed_count": failed,
            "message": f"Reindexed {success} documents ({failed} failed)",
        })


class QueryTimer:
    """Context manager for timing query execution."""

    def __init__(self):
        self.start_time: float = 0
        self.elapsed_ms: int = 0

    def __enter__(self):
        self.start_time = time.monotonic()
        return self

    def __exit__(self, *args):
        self.elapsed_ms = int((time.monotonic() - self.start_time) * 1000)
