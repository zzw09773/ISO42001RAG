"""
ISO 42001 Structured Audit Logger

Provides dual-output logging (local file + console) in JSON Lines format.
Covers ISO/IEC 42001 Annex A requirements:
  - A.4 Resource management (model name, token usage)
  - A.6 Lifecycle monitoring (response time, event types)
  - A.7 Data provenance (retrieved document sources)
  - A.9 Intended use & human oversight (scope check, user queries)
"""
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Structured audit logger for ISO 42001 compliance.
    Writes JSON Lines to a daily-rotated file and echoes to console.
    """

    # Genesis hash for the first entry of each log file's chain
    _GENESIS_HASH = "0" * 64

    # Audit timestamps & daily rotation use Taiwan local time (UTC+8) so that
    # log dates align with the operators' wall clock (ISO 27001 A.8.17 — the
    # reference time source is documented and consistent across records).
    _TPE_TZ = timezone(timedelta(hours=8))

    # Shared across ALL AuditLogger instances in this process. Multiple
    # instances write the same daily file (api.py, security_block node,
    # react_workflow), so the chain-tip cache AND the read→write→update
    # critical section MUST be shared and serialized — otherwise two
    # interleaved writes read the same tip and branch the chain (two records
    # pointing at the same prev_hash), breaking verify_integrity().
    _LAST_HASH: Dict[str, str] = {}
    _LOCK = threading.Lock()

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
        """Get today's log file path (daily rotation, UTC+8 calendar day)."""
        today = datetime.now(self._TPE_TZ).strftime("%Y-%m-%d")
        return self.log_dir / f"audit_{today}.jsonl"

    def _get_prev_hash(self, log_file: Path) -> str:
        """Return the entry_hash of the last record in the file (or genesis).

        Caller must hold _LOCK (called inside _write's critical section).

        Self-heal after an external clear: if the in-memory chain-tip cache
        holds a hash but the file was deleted or truncated to empty (e.g. a
        data reset ran while this process kept running), the cache is stale.
        Writing the cached hash as the next record's prev_hash would make the
        first line of the new file point at a now-gone entry, which
        verify_integrity reports as "broken at line 1" (a FALSE tamper
        alert). When the file has no content we therefore restart the chain
        from genesis and drop the stale cache entry. Normal operation
        (file has content, cache valid) is unchanged.
        """
        key = str(log_file)
        file_has_content = log_file.exists() and log_file.stat().st_size > 0
        cached = self._LAST_HASH.get(key)
        if cached is not None:
            if file_has_content:
                return cached
            # Stale cache vs emptied/missing file → external reset → genesis.
            self._LAST_HASH.pop(key, None)
            return self._GENESIS_HASH
        # Cold start: read the last line's entry_hash from disk
        if file_has_content:
            try:
                last = None
                with open(log_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            last = line
                if last:
                    return json.loads(last).get("entry_hash", self._GENESIS_HASH)
            except Exception:
                pass
        return self._GENESIS_HASH

    def _write(self, record: Dict[str, Any]) -> None:
        """Write a single audit record to file and console.

        ISO 27001 A.8.15 / A.5.28 — tamper-evident logging:
        Each record carries `prev_hash` and `entry_hash` forming a hash
        chain. entry_hash = SHA256(prev_hash + canonical_record). Any edit,
        deletion, or reordering of a past record breaks the chain and is
        detectable via verify_integrity(). The file is chmod 640 to remove
        world-read access (queries may contain personal data).
        """
        record["timestamp"] = datetime.now(self._TPE_TZ).isoformat()

        log_file = self._get_log_file()
        # Serialize read-tip → hash → append → update-tip so concurrent or
        # multi-instance writes can't branch the chain (see _LOCK docstring).
        try:
            with self._LOCK:
                prev_hash = self._get_prev_hash(log_file)
                record["prev_hash"] = prev_hash
                # Hash over the canonical record (entry_hash field not yet present)
                canonical = json.dumps(record, ensure_ascii=False, sort_keys=True)
                entry_hash = hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()
                record["entry_hash"] = entry_hash

                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                # Restrict permissions: owner rw, group r, others none (A.8.15)
                try:
                    os.chmod(log_file, 0o640)
                except OSError:
                    pass
                self._LAST_HASH[str(log_file)] = entry_hash
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

        # Echo to console
        event_type = record.get("event_type", "unknown")
        summary = self._format_console(record)
        self._console_logger.info(f"{event_type} | {summary}")

    @staticmethod
    def verify_integrity(log_file: Path) -> Dict[str, Any]:
        """Verify the hash chain of a log file (ISO 27001 A.5.28 evidence).

        Returns {"valid": bool, "total": int, "broken_at": Optional[int],
                 "reason": str}. broken_at is the 1-based line number of the
        first tampered record.
        """
        prev = AuditLogger._GENESIS_HASH
        total = 0
        try:
            with open(log_file, encoding="utf-8") as f:
                for i, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    total += 1
                    rec = json.loads(line)
                    stored_hash = rec.pop("entry_hash", None)
                    if rec.get("prev_hash") != prev:
                        return {"valid": False, "total": total, "broken_at": i,
                                "reason": f"prev_hash mismatch at line {i}"}
                    canonical = json.dumps(rec, ensure_ascii=False, sort_keys=True)
                    recomputed = hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest()
                    if recomputed != stored_hash:
                        return {"valid": False, "total": total, "broken_at": i,
                                "reason": f"entry_hash mismatch at line {i} (record was modified)"}
                    prev = stored_hash
        except FileNotFoundError:
            return {"valid": False, "total": 0, "broken_at": None, "reason": "file not found"}
        return {"valid": True, "total": total, "broken_at": None, "reason": "chain intact"}

    def _format_console(self, record: Dict[str, Any]) -> str:
        """Format a record for concise console output."""
        parts = []
        if record.get("request_id"):
            parts.append(f"request={str(record['request_id'])[:8]}")
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

    @staticmethod
    def _source_fields(
        request_id: str = "",
        source_app: str = "",
        openai_response_id: str = "",
        frontend_session_id: str = "",
        frontend_user_id: str = "",
        frontend_chat_id: str = "",
        frontend_message_id: str = "",
        frontend_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return optional source-correlation fields without breaking old logs."""
        fields: Dict[str, Any] = {}
        optional = {
            "request_id": request_id,
            "source_app": source_app,
            "openai_response_id": openai_response_id,
            "frontend_session_id": frontend_session_id,
            "frontend_user_id": frontend_user_id,
            "frontend_chat_id": frontend_chat_id,
            "frontend_message_id": frontend_message_id,
        }
        for key, value in optional.items():
            if value:
                fields[key] = value
        if frontend_metadata:
            fields["frontend_metadata"] = frontend_metadata
        return fields

    # ---- High-level event methods ----

    def log_rejection(
        self,
        session_id: str,
        user_query: str,
        reason: str = "out_of_scope",
        client_ip: str = "",
        request_id: str = "",
        source_app: str = "",
        openai_response_id: str = "",
        frontend_session_id: str = "",
        frontend_user_id: str = "",
        frontend_chat_id: str = "",
        frontend_message_id: str = "",
        frontend_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a rejected query (out of scope / ISO 42001 A.9)."""
        record = {
            "event_type": "rejection",
            "session_id": session_id,
            "client_ip": client_ip or "unknown",
            "user_query": user_query,
            "scope_check": "out_of_scope",
            "reason": reason,
        }
        record.update(self._source_fields(
            request_id=request_id,
            source_app=source_app,
            openai_response_id=openai_response_id,
            frontend_session_id=frontend_session_id,
            frontend_user_id=frontend_user_id,
            frontend_chat_id=frontend_chat_id,
            frontend_message_id=frontend_message_id,
            frontend_metadata=frontend_metadata,
        ))
        self._write(record)

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

    def log_security_alert(
        self,
        session_id: str,
        user_query: str,
        threat_type: str,
        reason: str,
        stage: str = "input",
        action_taken: str = "blocked",
        user_notified: bool = True,
        detection_method: str = "input_sanitizer",
        client_ip: str = "",
        request_id: str = "",
        source_app: str = "",
        openai_response_id: str = "",
        frontend_session_id: str = "",
        frontend_user_id: str = "",
        frontend_chat_id: str = "",
        frontend_message_id: str = "",
        frontend_metadata: Optional[Dict[str, Any]] = None,
        message_index: Optional[int] = None,
        message_role: Optional[str] = None,
        message_source: Optional[str] = None,
        wrapper_mode: bool = False,
    ) -> None:
        """Log a security alert (prompt injection / system probe / output leak).

        Extended fields document the FULL handling trace (ISO 42001 A.8/A.9),
        since a blocked request never reaches the LLM and thus has no normal
        processing record:
          - action_taken     : what the system did (blocked / redacted)
          - user_notified    : whether the user received a response message
                               (True since the streaming-blank bug was fixed)
          - detection_method : which guard caught it (input_sanitizer / output_filter)
        """
        record = {
            "event_type": "security_alert",
            "session_id": session_id,
            "client_ip": client_ip or "unknown",
            "user_query": user_query[:200],
            "threat_type": threat_type,
            "reason": reason,
            "stage": stage,
            "action_taken": action_taken,
            "user_notified": user_notified,
            "detection_method": detection_method,
        }
        record.update(self._source_fields(
            request_id=request_id,
            source_app=source_app,
            openai_response_id=openai_response_id,
            frontend_session_id=frontend_session_id,
            frontend_user_id=frontend_user_id,
            frontend_chat_id=frontend_chat_id,
            frontend_message_id=frontend_message_id,
            frontend_metadata=frontend_metadata,
        ))
        # pre-graph 攔截補充欄位（訊息定位與 wrapper 豁免狀態）。僅非 None 併入，
        # graph 既有呼叫不帶這些引數時 schema 維持相容（message_* 缺席）。
        for _k, _v in (("message_index", message_index), ("message_role", message_role),
                       ("message_source", message_source), ("wrapper_mode", wrapper_mode)):
            if _v is not None:
                record[_k] = _v
        self._write(record)

    def log_auth_event(
        self,
        event: str,
        api_key_prefix: str,
        path: str,
        reason: Optional[str] = None,
        client_ip: str = "",
        request_id: str = "",
        source_app: str = "",
        frontend_session_id: str = "",
        frontend_user_id: str = "",
        frontend_chat_id: str = "",
        frontend_message_id: str = "",
        frontend_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an authentication/authorization event (success or failure).

        client_ip is recorded explicitly (ISO 27001 A.8.15) so that even
        key-mode auth records carry the source address — failed access
        attempts must be attributable for brute-force / intrusion analysis.
        """
        record = {
            "event_type": f"auth_{event}",
            "client_ip": client_ip or "unknown",
            "api_key_prefix": api_key_prefix,
            "path": path,
            "reason": reason,
        }
        record.update(self._source_fields(
            request_id=request_id,
            source_app=source_app,
            frontend_session_id=frontend_session_id,
            frontend_user_id=frontend_user_id,
            frontend_chat_id=frontend_chat_id,
            frontend_message_id=frontend_message_id,
            frontend_metadata=frontend_metadata,
        ))
        self._write(record)

    def log_query(
        self,
        session_id: str,
        user_query: str,
        scope_check: str,
        model_name: str,
        retrieved_docs: Optional[List[str]] = None,
        tokens_used: Optional[int] = None,
        response_time_ms: Optional[int] = None,
        retrieval_doc_count: Optional[int] = None,
        citation_count: Optional[int] = None,
        retry_count: int = 0,
        anomaly_flags: Optional[List[str]] = None,
        client_ip: str = "",
        actions: Optional[List[str]] = None,
        model_response: str = "",
        request_id: str = "",
        source_app: str = "",
        openai_response_id: str = "",
        frontend_session_id: str = "",
        frontend_user_id: str = "",
        frontend_chat_id: str = "",
        frontend_message_id: str = "",
        frontend_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a user query event with extended monitoring metrics.

        `actions` documents the workflow steps the system actually took
        (ISO 42001 A.6 lifecycle monitoring — auditor must be able to
        reconstruct what the agent did, not just what it received).

        `model_response` records the LLM's full generated answer so the
        log distinguishes USER input (`user_query`) from MODEL output
        (`model_response`) — required for A.9 oversight and for
        faithfulness/citation auditing.

        Prompt provenance is captured via `prompt_version_hash` from
        `prompts.py:PROMPT_VERSIONS` so any approved prompt baseline change
        is detectable in the audit trail (A.4).
        """
        # Lazy import to avoid circular dependencies at module load
        try:
            from .prompts import PROMPT_VERSIONS, prompt_version_hash
            prompt_baseline = PROMPT_VERSIONS["SYSTEM_PROMPT_BASELINE"]
            phash = prompt_version_hash()
        except Exception:
            prompt_baseline = ""
            phash = ""
        record = {
            "event_type": "query",
            "session_id": session_id,
            "client_ip": client_ip or "unknown",
            "user_query": user_query,
            "model_response": model_response,
            "scope_check": scope_check,
            "model_name": model_name,
            "prompt_baseline": prompt_baseline,
            "prompt_version_hash": phash,
            "retrieved_docs": retrieved_docs or [],
            "tokens_used": tokens_used,
            "response_time_ms": response_time_ms,
            "retrieval_doc_count": retrieval_doc_count,
            "citation_count": citation_count,
            "retry_count": retry_count,
            "actions": actions or [],
            "anomaly_flags": anomaly_flags or [],
        }
        record.update(self._source_fields(
            request_id=request_id,
            source_app=source_app,
            openai_response_id=openai_response_id,
            frontend_session_id=frontend_session_id,
            frontend_user_id=frontend_user_id,
            frontend_chat_id=frontend_chat_id,
            frontend_message_id=frontend_message_id,
            frontend_metadata=frontend_metadata,
        ))
        self._write(record)


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
