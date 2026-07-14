"""Audit-log search and OpenWebUI correlation helpers."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from .audit_loader import filter_by_window, list_audit_files

_TPE_TZ = timezone(timedelta(hours=8))


def fetch_current_prompt_version(
    rag_api_url: Optional[str] = None,
    *,
    timeout: float = 2.0,
) -> dict:
    """Read the prompt version from the running RAG service.

    The runtime `/health` response is the source of truth. Monitoring must not
    import or duplicate the prompt registry because its image can otherwise
    disagree with the independently deployed rag-api image.
    """
    base_url = (rag_api_url or os.environ.get("RAG_API_URL", "http://rag-api:8000")).rstrip("/")
    source = f"{base_url}/health"
    try:
        response = requests.get(source, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        baseline = str(payload.get("prompt_baseline") or "").strip()
        version_hash = str(payload.get("prompt_version_hash") or "").strip()
        if not baseline or not version_hash:
            raise ValueError("RAG health response has no prompt version metadata")
        return {
            "available": True,
            "prompt_baseline": baseline,
            "prompt_version_hash": version_hash,
            "source": source,
            "error": "",
        }
    except Exception as exc:
        return {
            "available": False,
            "prompt_baseline": "",
            "prompt_version_hash": "",
            "source": source,
            "error": str(exc)[:200],
        }


def attach_prompt_version_status(result: dict, current_prompt: dict) -> None:
    """Compare each query log's prompt metadata with the running RAG state."""
    counts: Counter = Counter()
    current_available = bool(current_prompt.get("available"))
    current_baseline = str(current_prompt.get("prompt_baseline") or "")
    current_hash = str(current_prompt.get("prompt_version_hash") or "")

    for event in result.get("events") or []:
        if event.get("event_type") != "query":
            status = "not_applicable"
        elif not current_available:
            status = "unavailable"
        else:
            log_baseline = str(event.get("prompt_baseline") or "")
            log_hash = str(event.get("prompt_version_hash") or "")
            if not log_hash:
                status = "missing"
            elif log_hash != current_hash or (log_baseline and log_baseline != current_baseline):
                status = "mismatch"
            elif not log_baseline:
                status = "match_legacy"
            else:
                status = "match"
        event["_prompt_version_status"] = status
        counts[status] += 1

    result["prompt_version"] = current_prompt
    result.setdefault("summary", {})["by_prompt_version_status"] = dict(counts)


def parse_time(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), _TPE_TZ)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), _TPE_TZ)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TPE_TZ)
    return dt.astimezone(_TPE_TZ)


def danger_level(event: dict) -> str:
    et = event.get("event_type", "")
    if et == "security_alert":
        return "critical"
    if et == "auth_failure":
        return "warning"
    if event.get("anomaly_flags"):
        flags = " ".join(str(x) for x in event.get("anomaly_flags") or [])
        if "security_alert_burst" in flags:
            return "critical"
        return "warning"
    if et == "query" and isinstance(event.get("response_time_ms"), int):
        if event["response_time_ms"] > 60_000:
            return "warning"
    return "normal"


def danger_reason(event: dict) -> str:
    et = event.get("event_type", "")
    if et == "security_alert":
        return event.get("threat_type") or event.get("reason") or "security alert"
    if et == "auth_failure":
        return event.get("reason") or "auth failure"
    if event.get("anomaly_flags"):
        return ", ".join(str(x) for x in event.get("anomaly_flags") or [])
    if et == "query" and isinstance(event.get("response_time_ms"), int) and event["response_time_ms"] > 60_000:
        return f"latency {event['response_time_ms']} ms"
    return ""


def _iter_audit_records(audit_dir: Path, window_days: int) -> Iterable[dict]:
    files = filter_by_window(list_audit_files(audit_dir), window_days)
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                for line_no, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rec["_log_file"] = path.name
                    rec["_line_no"] = line_no
                    yield rec
        except FileNotFoundError:
            continue


def _contains_text(event: dict, q: str) -> bool:
    if not q:
        return True
    needle = q.lower()
    fields = [
        "event_type", "session_id", "request_id", "openai_response_id",
        "client_ip", "source_app", "frontend_session_id", "frontend_user_id",
        "frontend_chat_id", "frontend_message_id", "user_query",
        "model_response", "reason", "threat_type", "prompt_baseline",
        "prompt_version_hash",
    ]
    for field in fields:
        value = event.get(field)
        if value is not None and needle in str(value).lower():
            return True
    meta = event.get("frontend_metadata")
    return bool(meta and needle in json.dumps(meta, ensure_ascii=False).lower())


def search_audit_events(
    *,
    audit_dir: Path,
    window_days: int = 30,
    event_type: str = "",
    danger_only: bool = False,
    session_id: str = "",
    request_id: str = "",
    client_ip: str = "",
    q: str = "",
    from_ts: str = "",
    to_ts: str = "",
    limit: int = 200,
) -> dict:
    start = parse_time(from_ts)
    end = parse_time(to_ts)
    events: List[dict] = []
    total_seen = 0
    for event in _iter_audit_records(audit_dir, window_days):
        total_seen += 1
        ts = parse_time(event.get("timestamp"))
        if start and (ts is None or ts < start):
            continue
        if end and (ts is None or ts > end):
            continue
        if event_type and event.get("event_type") != event_type:
            continue
        if session_id and session_id not in str(event.get("session_id", "")):
            continue
        if request_id and request_id not in str(event.get("request_id", "")):
            continue
        if client_ip and client_ip not in str(event.get("client_ip", "")):
            continue
        level = danger_level(event)
        if danger_only and level == "normal":
            continue
        if not _contains_text(event, q):
            continue
        event["_danger_level"] = level
        event["_danger_reason"] = danger_reason(event)
        events.append(event)

    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    out = events[: max(1, min(limit, 1000))]
    return {
        "total_seen": total_seen,
        "matched": len(events),
        "returned": len(out),
        "events": out,
        "summary": {
            "by_event_type": dict(Counter(e.get("event_type", "unknown") for e in events)),
            "by_danger_level": dict(Counter(e.get("_danger_level", "normal") for e in events)),
        },
    }


def _walk_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_strings(item)
    elif isinstance(obj, dict):
        role = str(obj.get("role", "")).lower()
        content = obj.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            yield content
        for value in obj.values():
            yield from _walk_strings(value)


class OpenWebUICorrelator:
    def __init__(self, db_path: Optional[Path] = None):
        raw = db_path or Path(os.environ.get("OPENWEBUI_DB", "/openwebui_data/webui.db"))
        self.db_path = Path(raw)

    @property
    def available(self) -> bool:
        return self.db_path.exists()

    def find_matches(self, event: dict, *, window_seconds: int = 900, max_matches: int = 5) -> List[dict]:
        if not self.available:
            return []
        chat_id = (event.get("frontend_chat_id") or "").strip()
        query = (event.get("user_query") or "").strip()
        # 無硬 ID 也無查詢文字時無從配對
        if not chat_id and not query:
            return []
        event_ts = parse_time(event.get("timestamp"))
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=1)
            con.row_factory = sqlite3.Row

            # ① 硬關聯優先：OpenWebUI 轉發 X-OpenWebUI-Chat-Id 後，audit 帶 frontend_chat_id，
            # 直接以 chat.id 精準對應——連背景請求（後續建議問題、標題生成，無使用者訊息
            # 可比對）也能歸到同一對話。
            if chat_id:
                row = con.execute(
                    """
                    select c.id as chat_id, c.user_id, c.title, c.created_at, c.updated_at,
                           c.chat, u.email, u.name
                      from chat c left join user u on u.id = c.user_id
                     where c.id = ?
                    """,
                    (chat_id,),
                ).fetchone()
                if row is not None:
                    updated_at = parse_time(row["updated_at"])
                    delta_sec = (
                        abs(int((updated_at - event_ts).total_seconds()))
                        if event_ts and updated_at else None
                    )
                    return [{
                        "chat_id": row["chat_id"], "user_id": row["user_id"],
                        "user_email": row["email"], "user_name": row["name"],
                        "title": row["title"], "created_at": row["created_at"],
                        "updated_at": row["updated_at"], "delta_seconds": delta_sec,
                        "match_kind": "chat_id", "matched_text": "",
                    }]

            # ② 文字＋時間軟配對（未開啟 header 轉發、或舊紀錄的退路）
            if not query:
                return []
            rows = con.execute(
                """
                select c.id as chat_id, c.user_id, c.title, c.created_at, c.updated_at,
                       c.chat, u.email, u.name
                  from chat c
                  left join user u on u.id = c.user_id
                 order by c.updated_at desc
                 limit 500
                """
            ).fetchall()
        except Exception:
            return []
        finally:
            try:
                con.close()
            except Exception:
                pass

        matches: List[dict] = []
        q_lower = query.lower()
        for row in rows:
            raw_chat = row["chat"] or ""
            # OpenWebUI 以 ensure_ascii 儲存 chat JSON，CJK 在原始字串是 \uXXXX
            # 跳脫碼；直接對原始字串做子字串比對，中文查詢永遠配不到。先解碼 JSON、
            # 走訪各字串再比對（英文/數字查詢同樣可配）。
            decoded_strings: List[str]
            try:
                parsed = json.loads(raw_chat)
                decoded_strings = list(_walk_strings(parsed))
            except Exception:
                parsed = None
                decoded_strings = [raw_chat]
            matched_text = ""
            for text in decoded_strings:
                if q_lower in text.lower():
                    matched_text = text
                    break
            if not matched_text:
                continue
            updated_at = parse_time(row["updated_at"])
            delta_sec = None
            if event_ts and updated_at:
                delta_sec = abs(int((updated_at - event_ts).total_seconds()))
                if delta_sec > window_seconds and len(query) < 12:
                    continue
            matches.append({
                "chat_id": row["chat_id"],
                "user_id": row["user_id"],
                "user_email": row["email"],
                "user_name": row["name"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "delta_seconds": delta_sec,
                "match_kind": "text",
                "matched_text": matched_text[:500],
            })
            if len(matches) >= max_matches:
                break
        return matches


def attach_openwebui_matches(events: List[dict], correlator: Optional[OpenWebUICorrelator] = None) -> None:
    owui = correlator or OpenWebUICorrelator()
    if not owui.available:
        return
    for event in events:
        event["openwebui_matches"] = owui.find_matches(event)


def render_audit_page(result: dict, params: dict, *, openwebui_available: bool) -> str:
    events = result.get("events") or []
    summary = result.get("summary") or {}
    current_prompt = result.get("prompt_version") or {}

    def val(name: str) -> str:
        return escape(str(params.get(name, "") or ""))

    rows = []
    prompt_status_labels = {
        "match": ("normal", "一致"),
        "match_legacy": ("warning", "一致（舊 log 僅 hash）"),
        "mismatch": ("critical", "版本漂移"),
        "missing": ("warning", "log 未記錄"),
        "unavailable": ("warning", "無法取得實際版本"),
        "not_applicable": ("", "不適用"),
    }
    for event in events:
        level = event.get("_danger_level", "normal")
        q = event.get("user_query") or event.get("message") or event.get("reason") or ""
        ids = "<br>".join(
            f"<code>{escape(k)}</code>: {escape(str(v))}"
            for k, v in {
                "session": event.get("session_id"),
                "request": event.get("request_id"),
                "response": event.get("openai_response_id"),
                "front_chat": event.get("frontend_chat_id"),
            }.items() if v
        ) or "—"
        owui_bits = []
        for match in event.get("openwebui_matches") or []:
            delta = match.get("delta_seconds")
            delta_text = f" · Δ {delta}s" if delta is not None else ""
            # 硬關聯（chat_id 精準對應）與軟關聯（文字＋時間猜測）明確標示，稽核可分辨可信度
            kind = match.get("match_kind")
            kind_badge = ("<span class='badge normal'>chat_id 對應</span>" if kind == "chat_id"
                          else "<span class='badge warning'>文字/時間推測</span>")
            owui_bits.append(
                f"<div>{kind_badge} <code>{escape(str(match.get('chat_id')))}</code>"
                f" · {escape(str(match.get('user_email') or match.get('user_name') or 'unknown'))}"
                f"{delta_text}<br><span>{escape(str(match.get('title') or ''))}</span></div>"
            )
        prompt_status = str(event.get("_prompt_version_status") or "not_applicable")
        prompt_class, prompt_label = prompt_status_labels.get(
            prompt_status, ("warning", prompt_status)
        )
        log_baseline = str(event.get("prompt_baseline") or "")
        log_hash = str(event.get("prompt_version_hash") or "")
        runtime_baseline = str(current_prompt.get("prompt_baseline") or "")
        runtime_hash = str(current_prompt.get("prompt_version_hash") or "")
        if prompt_status == "not_applicable":
            prompt_bits = "<span class='muted'>不適用</span>"
        else:
            badge_class = f" {prompt_class}" if prompt_class else ""
            prompt_bits = (
                f"<span class='badge{badge_class}'>{escape(prompt_label)}</span>"
                f"<div><b>Log</b> <code>{escape(log_baseline or '—')}</code><br>"
                f"<code class='hash'>{escape(log_hash or '—')}</code></div>"
                f"<div><b>Runtime</b> <code>{escape(runtime_baseline or '—')}</code><br>"
                f"<code class='hash'>{escape(runtime_hash or '—')}</code></div>"
            )
        rows.append(
            "<tr>"
            f"<td class='ts'>{escape(str(event.get('timestamp', ''))[:19]).replace('T', ' ')}</td>"
            f"<td><span class='badge {escape(level)}'>{escape(level)}</span><br>{escape(event.get('_danger_reason', ''))}</td>"
            f"<td><code>{escape(str(event.get('event_type', '')))}</code><br>{escape(str(event.get('client_ip', '')))}</td>"
            f"<td>{ids}</td>"
            f"<td>{escape(str(q))[:600]}</td>"
            f"<td>{''.join(owui_bits) if owui_bits else '—'}</td>"
            f"<td>{prompt_bits}</td>"
            f"<td><code>{escape(str(event.get('_log_file', '')))}:{event.get('_line_no', '')}</code></td>"
            "</tr>"
        )

    by_type = summary.get("by_event_type") or {}
    by_level = summary.get("by_danger_level") or {}
    by_prompt = summary.get("by_prompt_version_status") or {}
    if current_prompt.get("available"):
        prompt_runtime_html = (
            "<div class='prompt-runtime normal-box'><b>執行中 Prompt</b> "
            f"baseline <code>{escape(str(current_prompt.get('prompt_baseline') or ''))}</code> "
            f"hash <code class='hash'>{escape(str(current_prompt.get('prompt_version_hash') or ''))}</code> "
            f"<span class='muted'>來源 {escape(str(current_prompt.get('source') or ''))}</span></div>"
        )
    else:
        prompt_runtime_html = (
            "<div class='prompt-runtime warning-box'><b>無法取得執行中 Prompt 版本</b> "
            f"<span>{escape(str(current_prompt.get('error') or '未取得 RAG health'))}</span></div>"
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>Audit Log Search</title>
<style>
body{{margin:0;background:#f6f8fc;color:#1a1f2c;font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;font-size:15px;line-height:1.7}}
.page{{max-width:1440px;margin:0 auto;padding:40px 48px 56px;background:#fff;min-height:100vh;border-left:1px solid #c9d1dc;border-right:1px solid #c9d1dc}}
h1{{font-size:24px;font-weight:900;margin:0}}
.title-en{{font-size:13px;font-weight:500;color:#5b6578;margin-left:10px}}
.report-rule{{border:0;border-top:2px solid #1a1f2c;margin:14px 0 10px}}
.sub{{color:#5b6578;font-size:13px;margin-bottom:18px}}
form{{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;align-items:end;border:1px solid #c9d1dc;padding:14px}}
label{{font-size:12px;font-weight:700;color:#4b5568}}
input,select{{width:100%;padding:8px;border:1px solid #c9d1dc;background:#fff;font-family:inherit;font-size:13px}}
button,.link{{display:inline-block;padding:9px 14px;border:1px solid #1e3a8a;background:#1e3a8a;color:#fff;text-decoration:none;font-weight:800;cursor:pointer;font-size:13px}}
.quick{{margin:12px 0;display:flex;gap:8px;flex-wrap:wrap}}
.quick a{{font-size:12px;color:#1e3a8a;border:1px solid #c9d1dc;padding:5px 10px;text-decoration:none;font-weight:700}}
.quick a:hover{{border-color:#1e3a8a}}
.stats{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;color:#4b5568;font-size:12px}}
.stats code{{background:#eef2f7;padding:2px 5px}}
.prompt-runtime{{margin:12px 0;padding:10px 12px;border:1px solid #c9d1dc;font-size:12px}}
.normal-box{{border-left:4px solid #166534}}.warning-box{{border-left:4px solid #92400e}}
.hash{{overflow-wrap:anywhere;word-break:break-all}}.muted{{color:#5b6578}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
th{{text-align:left;padding:8px;font-weight:700;border-top:2px solid #1a1f2c;border-bottom:1px solid #1a1f2c}}
td{{border-bottom:1px solid #e5eaf1;padding:8px;vertical-align:top;line-height:1.5}}
tr:nth-child(even) td{{background:#fafbfd}}
.ts{{white-space:nowrap;font-family:"JetBrains Mono","Consolas",monospace;color:#4b5568}}
.badge{{display:inline-block;padding:1px 8px;font-weight:900;font-size:11px;border:1px solid currentColor}}
.critical{{color:#991b1b}}.warning{{color:#92400e}}.normal{{color:#166534}}
.note{{font-size:12px;color:#5b6578;margin:12px 0;line-height:1.6}}
code{{font-family:"JetBrains Mono","Consolas",monospace}}
@media(max-width:980px){{form{{grid-template-columns:1fr 1fr}}table{{font-size:12px}}}}
@media print{{body{{background:#fff}}.page{{border:0;padding:14px;max-width:none}}form,.quick{{display:none}}}}
</style>
</head>
<body><div class="page">
<h1>稽核日誌搜尋<span class="title-en">Audit Log Search</span></h1>
<hr class="report-rule">
<div class="sub">查詢 RAG audit JSONL；danger 包含 security_alert、auth_failure、anomaly_flags 與 P95/單筆延遲異常。OpenWebUI DB：{"available" if openwebui_available else "not mounted"}</div>
{prompt_runtime_html}
<form method="get" action="audit">
<div><label>關鍵字</label><input name="q" value="{val('q')}" placeholder="query/session/request/ip"></div>
<div><label>事件類型</label><select name="event_type">
<option value="">全部</option>
{''.join(f'<option value="{escape(t)}" {"selected" if params.get("event_type")==t else ""}>{escape(t)}</option>' for t in ["query","rejection","security_alert","auth_success","auth_failure","upload","reindex"])}
</select></div>
<div><label>只看危險</label><select name="danger_only"><option value="0">否</option><option value="1" {"selected" if str(params.get("danger_only","0"))=="1" else ""}>是</option></select></div>
<div><label>session_id</label><input name="session_id" value="{val('session_id')}"></div>
<div><label>request_id</label><input name="request_id" value="{val('request_id')}"></div>
<div><label>client_ip</label><input name="client_ip" value="{val('client_ip')}"></div>
<div><label>起始時間</label><input name="from_ts" value="{val('from_ts')}" placeholder="2026-07-07T09:00"></div>
<div><label>結束時間</label><input name="to_ts" value="{val('to_ts')}" placeholder="2026-07-07T18:00"></div>
<div><label>天數</label><input name="window_days" value="{val('window_days') or '30'}"></div>
<div><label>筆數</label><input name="limit" value="{val('limit') or '200'}"></div>
<div><button type="submit">查詢</button></div>
</form>
<div class="quick">
<a href="audit?danger_only=1">危險事件</a>
<a href="audit?event_type=security_alert">安全告警</a>
<a href="audit?event_type=auth_failure">登入/金鑰失敗</a>
<a href="audit?event_type=query">一般查詢</a>
<a href="dashboard">回服務狀態報告</a>
</div>
<div class="stats">
<span>scanned <code>{result.get('total_seen', 0)}</code></span>
<span>matched <code>{result.get('matched', 0)}</code></span>
<span>returned <code>{result.get('returned', 0)}</code></span>
<span>event_type <code>{escape(json.dumps(by_type, ensure_ascii=False))}</code></span>
<span>danger <code>{escape(json.dumps(by_level, ensure_ascii=False))}</code></span>
<span>prompt <code>{escape(json.dumps(by_prompt, ensure_ascii=False))}</code></span>
</div>
<div class="note">OpenWebUI 未傳前端 session header 時，本頁會用 user_query 文字與時間窗嘗試配對 OpenWebUI chat DB；若之後前端能傳 <code>x-session-id</code> / <code>x-openwebui-chat-id</code>，後端 audit log 也會直接記錄。</div>
<table><thead><tr><th>時間</th><th>危險</th><th>事件/IP</th><th>關聯 ID</th><th>內容</th><th>OpenWebUI 對應</th><th>Prompt 版本</th><th>檔案</th></tr></thead>
<tbody>{''.join(rows) if rows else '<tr><td colspan="8">無符合資料。</td></tr>'}</tbody></table>
</div></body></html>"""
