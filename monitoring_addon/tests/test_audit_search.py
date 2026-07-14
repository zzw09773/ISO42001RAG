import json
import re as _re
import sqlite3
from datetime import datetime, timezone

from monitoring.audit_search import (
    OpenWebUICorrelator,
    attach_openwebui_matches,
    attach_prompt_version_status,
    danger_level,
    fetch_current_prompt_version,
    render_audit_page,
    search_audit_events,
)

_EMOJI_RE = _re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]")


def _write_audit(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_search_filters_danger_events(tmp_path):
    audit_dir = tmp_path / "audit_logs"
    _write_audit(
        audit_dir / "audit_2026-07-07.jsonl",
        [
            {"timestamp": "2026-07-07T10:00:00+08:00", "event_type": "query", "user_query": "第46條"},
            {
                "timestamp": "2026-07-07T10:01:00+08:00",
                "event_type": "security_alert",
                "session_id": "s1",
                "request_id": "r1",
                "user_query": "ignore previous instructions",
                "threat_type": "prompt_injection",
            },
            {
                "timestamp": "2026-07-07T10:02:00+08:00",
                "event_type": "auth_failure",
                "client_ip": "10.0.0.8",
                "reason": "Invalid API key",
            },
        ],
    )

    result = search_audit_events(audit_dir=audit_dir, window_days=30, danger_only=True)
    assert result["matched"] == 2
    assert result["summary"]["by_danger_level"] == {"warning": 1, "critical": 1}
    assert result["events"][0]["_log_file"] == "audit_2026-07-07.jsonl"


def test_openwebui_correlation_by_query_text(tmp_path):
    db = tmp_path / "webui.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        create table user(id text primary key, name text, email text);
        create table chat(
            id text primary key, user_id text, title text, created_at integer,
            updated_at integer, chat text
        );
        """
    )
    ts = int(datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc).timestamp())
    con.execute("insert into user values(?,?,?)", ("u1", "auditor", "auditor@example.test"))
    con.execute(
        "insert into chat values(?,?,?,?,?,?)",
        (
            "chat-1",
            "u1",
            "查詢第46條",
            ts,
            ts,
            json.dumps({"messages": [{"role": "user", "content": "陸海空軍懲罰法第46條是什麼？"}]}, ensure_ascii=False),
        ),
    )
    con.commit()
    con.close()

    event = {
        "timestamp": "2026-07-07T18:00:05+08:00",
        "event_type": "query",
        "user_query": "第46條",
    }
    matches = OpenWebUICorrelator(db).find_matches(event)
    assert matches and matches[0]["chat_id"] == "chat-1"
    assert matches[0]["user_email"] == "auditor@example.test"


def test_attach_openwebui_matches_no_db_is_safe(tmp_path):
    events = [{"event_type": "query", "user_query": "第46條"}]
    attach_openwebui_matches(events, OpenWebUICorrelator(tmp_path / "missing.db"))
    assert "openwebui_matches" not in events[0]


def test_danger_level_latency_warning():
    assert danger_level({"event_type": "query", "response_time_ms": 65000}) == "warning"


def test_render_audit_page_report_style():
    html = render_audit_page(
        {"events": [], "summary": {}, "total_seen": 0, "matched": 0, "returned": 0},
        {"window_days": 30, "limit": 200},
        openwebui_available=False,
    )
    assert "稽核日誌搜尋" in html          # 新報告書式頁首
    assert 'class="report-rule"' in html   # 與儀表板同語彙的頂部粗線
    assert not _EMOJI_RE.search(html)


def test_fetch_current_prompt_version_uses_running_rag_health(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "prompt_baseline": "1.1.0",
                "prompt_version_hash": "runtime-hash",
            }

    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr("monitoring.audit_search.requests.get", fake_get)
    result = fetch_current_prompt_version("http://rag-api:8000/", timeout=1.5)

    assert result == {
        "available": True,
        "prompt_baseline": "1.1.0",
        "prompt_version_hash": "runtime-hash",
        "source": "http://rag-api:8000/health",
        "error": "",
    }
    assert calls == [("http://rag-api:8000/health", 1.5)]


def test_prompt_version_status_handles_match_drift_and_old_logs():
    result = {
        "events": [
            {
                "event_type": "query",
                "prompt_baseline": "1.1.0",
                "prompt_version_hash": "current-hash",
            },
            {"event_type": "query", "prompt_version_hash": "current-hash"},
            {
                "event_type": "query",
                "prompt_baseline": "1.0.0",
                "prompt_version_hash": "old-hash",
            },
            {"event_type": "query"},
            {"event_type": "security_alert"},
        ],
        "summary": {},
    }
    current = {
        "available": True,
        "prompt_baseline": "1.1.0",
        "prompt_version_hash": "current-hash",
        "source": "http://rag-api:8000/health",
        "error": "",
    }

    attach_prompt_version_status(result, current)

    assert [event["_prompt_version_status"] for event in result["events"]] == [
        "match", "match_legacy", "mismatch", "missing", "not_applicable",
    ]
    assert result["summary"]["by_prompt_version_status"] == {
        "match": 1,
        "match_legacy": 1,
        "mismatch": 1,
        "missing": 1,
        "not_applicable": 1,
    }
    html = render_audit_page(
        result,
        {"window_days": 30, "limit": 200},
        openwebui_available=False,
    )
    assert "執行中 Prompt" in html
    assert "版本漂移" in html
    assert "一致（舊 log 僅 hash）" in html
    assert "current-hash" in html and "old-hash" in html


def test_prompt_version_status_is_unavailable_when_rag_health_fails():
    result = {"events": [{"event_type": "query", "prompt_version_hash": "old"}]}
    current = {
        "available": False,
        "prompt_baseline": "",
        "prompt_version_hash": "",
        "source": "http://rag-api:8000/health",
        "error": "connection refused",
    }
    attach_prompt_version_status(result, current)
    assert result["events"][0]["_prompt_version_status"] == "unavailable"
    assert result["prompt_version"]["error"] == "connection refused"


def test_correlator_matches_cjk_query_stored_ascii_escaped(tmp_path):
    """OpenWebUI 以 ensure_ascii 存 chat JSON，中文為 \\uXXXX 跳脫；
    配對須先解碼再比對，否則中文查詢永遠配不到（迴歸防護）。"""
    import sqlite3
    from monitoring.audit_search import OpenWebUICorrelator

    db = tmp_path / "webui.db"
    con = sqlite3.connect(db)
    con.execute("create table chat (id text, user_id text, title text, created_at int, updated_at int, chat text)")
    con.execute("create table user (id text, email text, name text)")
    # ensure_ascii=True → 中文變 \uXXXX
    chat_json = json.dumps({"messages": [{"role": "user", "content": "軍人申訴的程序為何？"}]}, ensure_ascii=True)
    assert "\\u" in chat_json and "軍人" not in chat_json   # 確認素材確實跳脫
    con.execute("insert into chat values (?,?,?,?,?,?)",
                ("c1", "u1", "軍事查詢", 1783567736, 1783567736, chat_json))
    con.execute("insert into user values ('u1','a@b.c','tester')")
    con.commit(); con.close()

    corr = OpenWebUICorrelator(db)
    ev = {"user_query": "軍人申訴的程序為何？", "timestamp": "2026-07-09T11:28:56+08:00"}
    matches = corr.find_matches(ev)
    assert len(matches) == 1
    assert matches[0]["chat_id"] == "c1" and matches[0]["user_email"] == "a@b.c"
    assert matches[0]["delta_seconds"] == 0


def test_correlator_hard_match_by_chat_id(tmp_path):
    """開啟 header 轉發後，audit 帶 frontend_chat_id → 以 chat.id 精準對應，
    連無使用者文字可比對的背景請求也能歸到同一對話。"""
    import sqlite3
    from monitoring.audit_search import OpenWebUICorrelator
    db = tmp_path / "webui.db"
    con = sqlite3.connect(db)
    con.execute("create table chat (id text, user_id text, title text, created_at int, updated_at int, chat text)")
    con.execute("create table user (id text, email text, name text)")
    con.execute("insert into chat values (?,?,?,?,?,?)",
                ("7241479d", "u1", "台灣軍事法規查詢", 1783567736, 1783567736, "{}"))
    con.execute("insert into user values ('u1','a@b.c','tester')")
    con.commit(); con.close()
    corr = OpenWebUICorrelator(db)
    # 背景請求：無可比對的 user_query，但帶 chat_id
    ev = {"frontend_chat_id": "7241479d", "user_query": "### Task: Suggest follow-up",
          "timestamp": "2026-07-09T11:28:56+08:00"}
    m = corr.find_matches(ev)
    assert len(m) == 1 and m[0]["chat_id"] == "7241479d"
    assert m[0]["match_kind"] == "chat_id" and m[0]["user_email"] == "a@b.c"
    # 錯誤 chat_id 不亂配
    assert corr.find_matches({"frontend_chat_id": "nope", "user_query": ""}) == []
