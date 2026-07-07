"""
Live Service Status Addon — FastAPI (port 8200 by default).

Port 8200 was chosen to avoid conflicts with:
  - RAG API           (port 8000)
  - embed-proxy       (port 8100) ← RAG main system uses this
  - common VSCode forwarded ports (8000 / 8100)

Endpoints:
  GET /                       → 302 redirect to /dashboard
  GET /health                 → liveness probe
  GET /dashboard              → live-rendered service-status dashboard
  GET /v1/dashboard/data      → JSON payload (machine-readable)
  GET /v1/drift               → backward-compatible health report alias
  GET /v1/extended-vv         → latest extended-V&V report from data/reports/
  GET /v1/alerts              → recent alerts (default 24h)
  GET /v1/alerts/summary      → per-severity counts

Background alerting loops (added v2.7):
  - every ALERT_ANOMALY_INTERVAL_SEC   (default 300)  scan audit anomaly_flags
  - every ALERT_DRIFT_INTERVAL_SEC     (default 900)  run health check, alert if worsened
  - every ALERT_INTEGRITY_INTERVAL_SEC (default 3600) verify audit hash chain

This service is **independent** of RAG/api.py. It runs on a different port,
reads only from RAG/data/audit_logs/ and the golden dataset, and never
writes to RAG/. Authentication is intentionally omitted — deploy behind
an internal-network proxy or add Bearer auth here if needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from monitoring.dashboard_data import build_payload
from monitoring.dashboard_render import render_dashboard
from monitoring.audit_search import (
    OpenWebUICorrelator,
    attach_openwebui_matches,
    render_audit_page,
    search_audit_events,
)
from monitoring.alerting import AlertSink
from monitoring.alert_checkers import AnomalyChecker, HealthChecker, IntegrityChecker
from monitoring.availability import AvailabilityProbe
from monitoring.config import PROBE_INTERVAL_SEC, PROBE_FAIL_CONFIRM, AVAIL_LOG_ROTATE_MB

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")

app = FastAPI(
    title="ISO 42001 Service Status Addon",
    description="Live service health / V&V / metrics dashboard (read-only, decoupled from RAG/)",
    version="0.2.0",
)

# ---------------------------------------------------------------------------
# Shared paths (resolved once at import; loops + endpoints both use these)
# ---------------------------------------------------------------------------

_RAG_DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", "/rag_data"))
_AUDIT_DIR = _RAG_DATA_DIR / "audit_logs"
_OPENWEBUI_DB = Path(os.environ.get("OPENWEBUI_DB", "/openwebui_data/webui.db"))
_ADDON_DATA = Path(__file__).resolve().parent.parent / "data"
_ALERT_DIR = _ADDON_DATA
_REPORTS_DIR = _ADDON_DATA / "reports"

# Singleton sink for the whole service so dedup state and SMTP config are shared
_SINK = AlertSink(alert_dir=_ALERT_DIR)


def _resolve_paths(audit_dir: Optional[str], golden: Optional[str], vv: Optional[str]):
    return (
        Path(audit_dir) if audit_dir else None,
        Path(golden) if golden else None,
        Path(vv) if vv else None,
    )


# ---------------------------------------------------------------------------
# Background alert loops
# ---------------------------------------------------------------------------

async def _periodic(name: str, interval_sec: int, fn):
    """Run `fn()` every `interval_sec`. Survives exceptions."""
    # Initial small delay so all three loops don't fire at once on boot
    await asyncio.sleep(min(30, interval_sec // 4))
    while True:
        try:
            n = await asyncio.to_thread(fn)
            if n:
                logger.info(f"[{name}] emitted {n} alert(s)")
        except Exception as e:
            logger.error(f"[{name}] check failed: {e}", exc_info=True)
        await asyncio.sleep(interval_sec)


_LOOP_TASKS: list = []


@app.on_event("startup")
async def _start_alert_loops():
    if os.environ.get("ALERT_LOOPS_DISABLED", "").lower() in ("true", "1", "yes"):
        logger.warning("ALERT_LOOPS_DISABLED=true — background alerting NOT started")
        return

    anomaly_sec = int(os.environ.get("ALERT_ANOMALY_INTERVAL_SEC", "300"))
    drift_sec = int(os.environ.get("ALERT_DRIFT_INTERVAL_SEC", "900"))
    integrity_sec = int(os.environ.get("ALERT_INTEGRITY_INTERVAL_SEC", "3600"))

    anomaly = AnomalyChecker(_AUDIT_DIR, _SINK, window_minutes=anomaly_sec // 60)
    health = HealthChecker(
        sink=_SINK,
        audit_dir=_AUDIT_DIR,
        output_dir=_REPORTS_DIR,
    )
    integrity = IntegrityChecker(_AUDIT_DIR, _SINK, data_dir=_ADDON_DATA)
    availability = AvailabilityProbe(
        _SINK, _ADDON_DATA, fail_confirm=PROBE_FAIL_CONFIRM, rotate_mb=AVAIL_LOG_ROTATE_MB)

    _LOOP_TASKS.append(asyncio.create_task(_periodic("anomaly", anomaly_sec, anomaly.check)))
    _LOOP_TASKS.append(asyncio.create_task(_periodic("health", drift_sec, health.check)))
    _LOOP_TASKS.append(asyncio.create_task(_periodic("integrity", integrity_sec, integrity.check)))
    _LOOP_TASKS.append(asyncio.create_task(_periodic("availability", PROBE_INTERVAL_SEC, availability.check)))

    sm = _SINK.smtp
    smtp_status = (
        f"SMTP enabled → {sm.host}:{sm.port} → {sm.recipients}"
        if sm.enabled else "SMTP disabled (set ALERT_SMTP_HOST + ALERT_RECIPIENTS to enable)"
    )
    logger.info(
        f"Alerting started: anomaly={anomaly_sec}s drift={drift_sec}s "
        f"integrity={integrity_sec}s | {smtp_status}"
    )


@app.on_event("shutdown")
async def _stop_alert_loops():
    for t in _LOOP_TASKS:
        t.cancel()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "monitoring_addon",
        "alerting_active": len(_LOOP_TASKS) > 0,
    }


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    window_days: int = Query(30, ge=1, le=365),
    audit_dir: Optional[str] = None,
    golden: Optional[str] = None,
    vv_report: Optional[str] = None,
):
    a, g, v = _resolve_paths(audit_dir, golden, vv_report)
    payload = build_payload(
        audit_dir=a, golden_path=g, vv_report_path=v, window_days=window_days
    )
    # Inject alerts into payload so dashboard_render can render the banner
    payload["alerts"] = {
        "recent": _SINK.recent(hours=24, max_n=20),
        "counts_24h": _SINK.severity_counts(hours=24),
        "smtp_enabled": _SINK.smtp.enabled,
    }
    return HTMLResponse(render_dashboard(payload))


@app.get("/audit", response_class=HTMLResponse)
async def audit_search_page(
    window_days: int = Query(30, ge=1, le=365),
    event_type: str = "",
    danger_only: int = Query(0, ge=0, le=1),
    session_id: str = "",
    request_id: str = "",
    client_ip: str = "",
    q: str = "",
    from_ts: str = "",
    to_ts: str = "",
    limit: int = Query(200, ge=1, le=1000),
    audit_dir: Optional[str] = None,
):
    a = Path(audit_dir) if audit_dir else _AUDIT_DIR
    result = search_audit_events(
        audit_dir=a,
        window_days=window_days,
        event_type=event_type,
        danger_only=bool(danger_only),
        session_id=session_id,
        request_id=request_id,
        client_ip=client_ip,
        q=q,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
    )
    correlator = OpenWebUICorrelator(_OPENWEBUI_DB)
    attach_openwebui_matches(result["events"], correlator)
    params = {
        "window_days": window_days,
        "event_type": event_type,
        "danger_only": str(danger_only),
        "session_id": session_id,
        "request_id": request_id,
        "client_ip": client_ip,
        "q": q,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "limit": limit,
    }
    return HTMLResponse(render_audit_page(result, params, openwebui_available=correlator.available))


@app.get("/v1/dashboard/data")
async def dashboard_data(
    window_days: int = Query(30, ge=1, le=365),
    audit_dir: Optional[str] = None,
    golden: Optional[str] = None,
    vv_report: Optional[str] = None,
):
    a, g, v = _resolve_paths(audit_dir, golden, vv_report)
    payload = build_payload(
        audit_dir=a, golden_path=g, vv_report_path=v, window_days=window_days
    )
    payload["alerts"] = {
        "recent": _SINK.recent(hours=24, max_n=50),
        "counts_24h": _SINK.severity_counts(hours=24),
        "smtp_enabled": _SINK.smtp.enabled,
    }
    return JSONResponse(payload)


@app.get("/v1/audit/events")
async def audit_events(
    window_days: int = Query(30, ge=1, le=365),
    event_type: str = "",
    danger_only: bool = False,
    session_id: str = "",
    request_id: str = "",
    client_ip: str = "",
    q: str = "",
    from_ts: str = "",
    to_ts: str = "",
    limit: int = Query(200, ge=1, le=1000),
    correlate_openwebui: bool = True,
    audit_dir: Optional[str] = None,
):
    a = Path(audit_dir) if audit_dir else _AUDIT_DIR
    result = search_audit_events(
        audit_dir=a,
        window_days=window_days,
        event_type=event_type,
        danger_only=danger_only,
        session_id=session_id,
        request_id=request_id,
        client_ip=client_ip,
        q=q,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
    )
    if correlate_openwebui:
        attach_openwebui_matches(result["events"], OpenWebUICorrelator(_OPENWEBUI_DB))
    result["openwebui_db_available"] = _OPENWEBUI_DB.exists()
    return JSONResponse(result)


def _latest_report_payload() -> dict:
    """Most recent health_*.json (falls back to legacy drift_*.json one version)."""
    reports = sorted(_REPORTS_DIR.glob("health_*.json")) or sorted(_REPORTS_DIR.glob("drift_*.json"))
    if not reports:
        raise HTTPException(404, "No health report found. Run scripts/run_health_check.py first.")
    return json.loads(reports[-1].read_text(encoding="utf-8"))


@app.get("/v1/health")
async def latest_health():
    """Return contents of the most recent health_*.json in data/reports/."""
    return JSONResponse(_latest_report_payload())


@app.get("/v1/drift")
async def latest_drift_alias():
    """Deprecated alias of /v1/health — identical payload, removed next version."""
    return JSONResponse(
        _latest_report_payload(),
        headers={"Deprecation": "true", "Warning": '299 - "use /v1/health"'},
    )


@app.get("/v1/extended-vv")
async def latest_extended_vv():
    """Return contents of the most recent extended_vv_*.json."""
    reports = sorted(
        (Path(__file__).resolve().parent.parent / "data" / "reports").glob("extended_vv_*.json")
    )
    if not reports:
        raise HTTPException(404, "No extended V&V report found. Run scripts/run_extended_vv.py first.")
    return JSONResponse(json.loads(reports[-1].read_text(encoding="utf-8")))


@app.get("/v1/alerts")
async def list_alerts(hours: int = Query(24, ge=1, le=720)):
    """Recent alerts within the last `hours` (max 30 days)."""
    return JSONResponse({
        "hours": hours,
        "counts": _SINK.severity_counts(hours=hours),
        "alerts": _SINK.recent(hours=hours, max_n=200),
    })


@app.get("/v1/alerts/summary")
async def alert_summary():
    """Counts for the dashboard top banner."""
    return JSONResponse({
        "counts_1h": _SINK.severity_counts(hours=1),
        "counts_24h": _SINK.severity_counts(hours=24),
        "smtp_enabled": _SINK.smtp.enabled,
    })


@app.post("/v1/alerts/test")
async def emit_test_alert(severity: str = Query("info", regex="^(info|warning|critical)$")):
    """Emit one test alert through the singleton sink.

    Used by deployment runbook H-5 to verify the full pipeline:
    sink → jsonl → SSE subscribers → SMTP (if configured). Bypasses the
    periodic loops so an operator can prove the wiring without waiting
    for scheduled drift/anomaly/integrity checks.

    Test alerts use a unique `dedup_key` per call so they're never suppressed.
    """
    import time
    from monitoring.alerting import Alert
    alert = Alert(
        severity=severity,
        source="self_test",
        title="部署驗證測試告警",
        message=f"由 POST /v1/alerts/test 觸發的 {severity} 等級測試告警。"
                f"若 SSE 訂閱者收到此筆、儀表板紅燈出現、SMTP 收件人收到信，"
                f"代表 alert pipeline 端到端正常。",
        evidence={"trigger": "manual", "endpoint": "/v1/alerts/test"},
        dedup_key=f"self_test:{time.time_ns()}",  # always unique
    )
    emitted = _SINK.emit(alert)
    return JSONResponse({
        "emitted": emitted,
        "severity": severity,
        "subscriber_count": _SINK.subscriber_count,
        "smtp_enabled": _SINK.smtp.enabled,
    })


# ---------------------------------------------------------------------------
# SSE: real-time alert stream (replaces 30s meta refresh)
# ---------------------------------------------------------------------------

_SSE_KEEPALIVE_SEC = 15  # bound by typical nginx proxy_read_timeout


@app.get("/v1/alerts/stream")
async def alerts_stream():
    """Server-Sent Events stream of alerts.

    On connect we send a `hello` event with the current 24h counts so the
    client can sync banner pills. Thereafter each `emit()` from any of the
    three check loops fans out to all subscribers within milliseconds —
    no polling, no full-page refresh.

    Connection keepalive: every 15s we emit `: keepalive\\n\\n` as an
    SSE comment so reverse-proxies don't kill the idle connection. Browsers
    auto-reconnect with default retry on transient drop.
    """
    loop = asyncio.get_running_loop()
    queue = _SINK.subscribe(loop)

    async def event_gen():
        # First frame: sync banner state. Client uses this on initial connect
        # and on every reconnect to re-baseline counters.
        try:
            hello = {
                "type": "hello",
                "counts_24h": _SINK.severity_counts(hours=24),
                "smtp_enabled": _SINK.smtp.enabled,
                "subscriber_count": _SINK.subscriber_count,
            }
            yield f"event: hello\ndata: {json.dumps(hello, ensure_ascii=False)}\n\n"

            while True:
                try:
                    alert = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SEC)
                except asyncio.TimeoutError:
                    # SSE comment for keepalive (browsers ignore comments)
                    yield ": keepalive\n\n"
                    continue
                yield f"event: alert\ndata: {json.dumps(alert, ensure_ascii=False)}\n\n"
        finally:
            _SINK.unsubscribe(queue)
            logger.info(f"SSE client disconnected (remaining: {_SINK.subscriber_count})")

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            # Disable nginx buffering so frames flush immediately
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MONITORING_PORT", "8200"))
    uvicorn.run(app, host="0.0.0.0", port=port)
