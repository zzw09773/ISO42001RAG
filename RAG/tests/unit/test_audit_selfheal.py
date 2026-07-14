"""
Audit hash-chain self-heal — ISO 27001 A.5.28

Regression test for the intranet trial bug: when the audit log file was
cleared (data reset) while rag-api kept running, the in-memory chain-tip
cache (_LAST_HASH) was stale, so the next record's prev_hash pointed at a
now-deleted entry → verify_integrity reported "broken at line 1" → a FALSE
critical "audit chain broken" alert fired every hour.

Fix: _get_prev_hash restarts from genesis when the file is empty/missing,
even if the cache holds a value.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rag_system.core.audit_logger import AuditLogger


def _first_line_prev(p: Path) -> str:
    import json
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line:
            return json.loads(line).get("prev_hash")
    return ""


def _latest(d: Path) -> Path:
    return sorted(d.glob("audit_*.jsonl"))[-1]


def test_normal_chain_is_valid(tmp_path):
    al = AuditLogger(tmp_path / "audit_logs")
    for i in range(3):
        al._write({"event_type": "query", "user_query": f"q{i}"})
    f = _latest(tmp_path / "audit_logs")
    assert AuditLogger.verify_integrity(f)["valid"] is True


def test_selfheal_after_truncate_without_restart(tmp_path):
    """File cleared (truncated) while process keeps stale cache → must self-heal."""
    al = AuditLogger(tmp_path / "audit_logs")
    for i in range(3):
        al._write({"event_type": "query", "user_query": f"q{i}"})
    f = _latest(tmp_path / "audit_logs")
    f.write_text("")  # external clear, cache NOT cleared
    al._write({"event_type": "query", "user_query": "first after reset"})
    assert _first_line_prev(f) == AuditLogger._GENESIS_HASH
    assert AuditLogger.verify_integrity(f)["valid"] is True


def test_selfheal_after_delete_without_restart(tmp_path):
    al = AuditLogger(tmp_path / "audit_logs")
    al._write({"event_type": "query", "user_query": "q0"})
    f = _latest(tmp_path / "audit_logs")
    f.unlink()  # external delete, cache NOT cleared
    al._write({"event_type": "query", "user_query": "first after delete"})
    assert _first_line_prev(f) == AuditLogger._GENESIS_HASH
    assert AuditLogger.verify_integrity(f)["valid"] is True


def test_continued_writing_still_chains(tmp_path):
    """The fix must NOT break normal continuation (no external clear)."""
    al = AuditLogger(tmp_path / "audit_logs")
    al._write({"event_type": "query", "user_query": "q0"})
    al._write({"event_type": "query", "user_query": "q1"})
    f = _latest(tmp_path / "audit_logs")
    r = AuditLogger.verify_integrity(f)
    assert r["valid"] is True and r["total"] == 2
    # second record's prev must be the first record's entry_hash (real chain)
    assert _first_line_prev(f) == AuditLogger._GENESIS_HASH


def test_query_log_keeps_source_correlation_fields(tmp_path):
    al = AuditLogger(tmp_path / "audit_logs")
    al.log_query(
        session_id="backend-session",
        user_query="第46條是什麼？",
        scope_check="in_scope",
        model_name="rag-agent",
        request_id="req-123",
        source_app="openwebui",
        openai_response_id="chatcmpl-req-123",
        frontend_chat_id="chat-abc",
        frontend_user_id="user-abc",
        frontend_metadata={"headers": {"user-agent": "OpenWebUI"}},
    )
    f = _latest(tmp_path / "audit_logs")
    import json
    rec = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    assert rec["request_id"] == "req-123"
    assert rec["source_app"] == "openwebui"
    assert rec["openai_response_id"] == "chatcmpl-req-123"
    assert rec["frontend_chat_id"] == "chat-abc"
    assert rec["frontend_metadata"]["headers"]["user-agent"] == "OpenWebUI"
    assert rec["prompt_baseline"] == "1.1.0"
    assert rec["prompt_version_hash"] == (
        "e61133c0a264b08604706292ba2dbf59b3092e1d9208b1e5c1f971b88c79dc3c"
    )
