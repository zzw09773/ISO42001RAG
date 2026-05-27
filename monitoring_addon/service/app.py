"""
Live Monitoring Dashboard Service — FastAPI (port 8200 by default).

Port 8200 was chosen to avoid conflicts with:
  - RAG API           (port 8000)
  - embed-proxy       (port 8100) ← RAG main system uses this
  - common VSCode forwarded ports (8000 / 8100)

Endpoints:
  GET /                       → 302 redirect to /dashboard
  GET /health                 → liveness probe
  GET /dashboard              → live-rendered HTML dashboard
  GET /v1/dashboard/data      → JSON payload (machine-readable)
  GET /v1/drift               → latest drift report only
  GET /v1/extended-vv         → latest extended-V&V report from data/reports/

This service is **independent** of RAG/api.py. It runs on a different port,
reads only from RAG/data/audit_logs/ and the golden dataset, and never
writes to RAG/. Authentication is intentionally omitted — deploy behind
an internal-network proxy or add Bearer auth here if needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from monitoring.dashboard_data import build_payload
from monitoring.dashboard_render import render_dashboard

app = FastAPI(
    title="ISO 42001 Monitoring Addon",
    description="Live drift / V&V / metrics dashboard (read-only, decoupled from RAG/)",
    version="0.1.0",
)


def _resolve_paths(audit_dir: Optional[str], golden: Optional[str], vv: Optional[str]):
    return (
        Path(audit_dir) if audit_dir else None,
        Path(golden) if golden else None,
        Path(vv) if vv else None,
    )


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "monitoring_addon"}


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
    return HTMLResponse(render_dashboard(payload))


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
    return JSONResponse(payload)


@app.get("/v1/drift")
async def latest_drift():
    """Return contents of the most recent drift_*.json in data/reports/."""
    reports = sorted(
        (Path(__file__).resolve().parent.parent / "data" / "reports").glob("drift_*.json")
    )
    if not reports:
        raise HTTPException(404, "No drift report found. Run scripts/run_drift_detection.py first.")
    return JSONResponse(json.loads(reports[-1].read_text(encoding="utf-8")))


@app.get("/v1/extended-vv")
async def latest_extended_vv():
    """Return contents of the most recent extended_vv_*.json."""
    reports = sorted(
        (Path(__file__).resolve().parent.parent / "data" / "reports").glob("extended_vv_*.json")
    )
    if not reports:
        raise HTTPException(404, "No extended V&V report found. Run scripts/run_extended_vv.py first.")
    return JSONResponse(json.loads(reports[-1].read_text(encoding="utf-8")))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MONITORING_PORT", "8200"))
    uvicorn.run(app, host="0.0.0.0", port=port)
