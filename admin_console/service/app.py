"""維運管理台 FastAPI 服務。依賴全部可注入，宿主測試不需 docker/httpx 實體。

帳密紅線：ADMIN_USERNAME/ADMIN_PASSWORD 只從環境變數讀（來源為 gitignored .env），
絕不硬編碼；比對用 secrets.compare_digest。
"""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from admincore import cardauth
from admincore.challenge_store import ChallengeStore
from admincore.dockerops import DockerOps
from admincore.envstore import EnvStore, SettingError, SETTINGS, WHITELIST, validate
from admincore.jobs import JobBusy, JobManager
from admincore.render import render_admin_page, render_login_page
from admincore.reports import flip_compare, list_reports

CONTAINER_REPORTS = "/app/data/reports"
RAG_CONTAINER = "ISO42001_rag_api"
MON_CONTAINER = "ISO42001_monitoring"

JOB_CATALOG: dict[str, dict] = {
    "online_vv":    {"container": MON_CONTAINER, "cmd": ["python3", "scripts/run_online_vv.py"]},
    "ragas":        {"container": MON_CONTAINER, "cmd": ["python3", "scripts/run_ragas_evaluation.py"]},
    "regression_gate": {"container": MON_CONTAINER, "params": ["baseline", "current", "tag"]},
    "attribution":  {"container": MON_CONTAINER, "params": ["vv_report"]},
    "reindex_full": {"container": RAG_CONTAINER, "cmd": ["python3", "scripts/reindex.py"]},
    "version_snapshot": {"container": RAG_CONTAINER, "params": ["message", "operator", "version"]},
}


def _default_http_post(url: str, **kw):
    import httpx
    try:
        r = httpx.post(url, timeout=2, **kw)
        try:
            body = r.json()
        except ValueError:
            body = None
        return {"status_code": r.status_code, "ok": r.status_code < 400, "json": body}
    except Exception as e:
        return {"status_code": 502, "ok": False, "json": None, "error": str(e)}


def _default_http_get(url: str, **kw):
    import httpx
    try:
        r = httpx.get(url, timeout=2, **kw)
        try:
            body = r.json()
        except ValueError:
            body = None
        return {"status_code": r.status_code, "ok": r.status_code < 400, "json": body}
    except Exception as e:
        return {"status_code": 502, "ok": False, "json": None, "error": str(e)}


def _default_http_probe(url: str) -> dict:
    """連線探測：任何 HTTP 回應＝可連線（rag-api 重啟後也能連）；
    連線層錯誤（打錯 host/port）＝不可連線。timeout 稍長給慢啟動的後端。"""
    import httpx
    try:
        r = httpx.get(url, timeout=4)
        return {"reachable": True, "status": r.status_code, "error": None}
    except Exception as e:
        return {"reachable": False, "status": None, "error": type(e).__name__}


def _safe_report_name(reports_dir: Path, name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and (reports_dir / name).is_file()


def _build_cmd(name: str, form: dict, reports_dir: Path) -> list[str] | None:
    """需要參數的 job 組 cmd；參數不合法回 None。"""
    if name == "regression_gate":
        base, cur = form.get("baseline", ""), form.get("current", "")
        if not (_safe_report_name(reports_dir, base) and _safe_report_name(reports_dir, cur)):
            return None
        return ["python3", "scripts/run_regression_gate.py",
                "--baseline", f"{CONTAINER_REPORTS}/{base}",
                "--current", f"{CONTAINER_REPORTS}/{cur}",
                "--tag", form.get("tag") or "admin-ui"]
    if name == "attribution":
        rep = form.get("vv_report", "")
        if not _safe_report_name(reports_dir, rep):
            return None
        return ["python3", "scripts/run_attribution.py",
                "--vv-report", f"{CONTAINER_REPORTS}/{rep}"]
    if name == "version_snapshot":
        return ["python3", "scripts/version_tracker.py", "snapshot",
                "-m", form.get("message", ""), "-o", form.get("operator", ""),
                "-v", form.get("version", "")]
    return list(JOB_CATALOG[name]["cmd"])


def _default_verify_card(signed_b64: str, expected_nonce: str):
    return cardauth.verify_pkcs7_signature(signed_b64, expected_nonce)
    # 注意：以 Task 5 移植後的實際簽名為準（參數名/順序照原始碼），此處為預設包裝。


def create_app(env_store: EnvStore, job_manager: JobManager, dockerops: DockerOps,
               reports_dir: Path, monitoring_url: str, rag_api_url: str,
               card_serials: set[str], admin_user: str, admin_password: str,
               password_fallback: bool, verify_card=None,
               http_post=None, http_get=None, http_probe=None) -> FastAPI:
    app = FastAPI(title="ISO 42001 admin console", docs_url=None, redoc_url=None)
    post = http_post or _default_http_post
    get = http_get or _default_http_get
    probe = http_probe or _default_http_probe
    verify = verify_card or _default_verify_card
    reports_dir = Path(reports_dir)
    sessions: set[str] = set()   # 記憶體 session：容器重啟即全登出（可接受）
    challenges = ChallengeStore()

    _PUBLIC = {"/login", "/api/auth/card/challenge", "/api/auth/card/verify"}

    def _new_session_response(target: str = "/"):
        token = secrets.token_urlsafe(32)
        sessions.add(token)
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie("admin_session", token, httponly=True, samesite="lax")
        return resp

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        if request.url.path in _PUBLIC or request.cookies.get("admin_session") in sessions:
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "未登入"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(render_login_page(password_fallback=password_fallback))

    @app.get("/api/auth/card/challenge")
    async def card_challenge():
        token, nonce = challenges.issue()
        return {"challenge_token": token, "nonce": nonce}

    @app.post("/api/auth/card/verify")
    async def card_verify(request: Request):
        form = dict(await request.form())
        nonce = challenges.consume(str(form.get("challenge_token", "")))
        if nonce is None:
            return JSONResponse({"error": "challenge 無效或已過期，請重新插卡"}, status_code=401)
        try:
            claims = verify(str(form.get("signed_data", "")), nonce)
        except Exception:
            return HTMLResponse(render_login_page(password_fallback=password_fallback,
                                                  error="憑證卡驗證失敗"))
        emp = str(getattr(claims, "employee_id", "") or "")
        if emp not in card_serials:
            return HTMLResponse(render_login_page(
                password_fallback=password_fallback,
                error=f"此卡員編 {emp} 不在管理台白名單——請將其加入 .env 的 ADMIN_CARD_SERIALS 後重試"))
        job_manager.log_change({"kind": "login", "method": "card", "employee_id": emp})
        return _new_session_response()

    @app.post("/login")
    async def login(request: Request):
        if not password_fallback:
            return JSONResponse({"error": "帳密登入未啟用（break-glass 需設 ENABLE_PASSWORD_FALLBACK=true）"},
                                status_code=403)
        form = dict(await request.form())
        user_ok = secrets.compare_digest(str(form.get("username", "")), admin_user)
        pass_ok = secrets.compare_digest(str(form.get("password", "")), admin_password)
        if not (user_ok and pass_ok):   # 兩者都比完才判定，不短路
            time.sleep(0.5)
            return HTMLResponse(render_login_page(password_fallback=True, error="帳號或密碼錯誤"))
        job_manager.log_change({"kind": "login", "method": "password_fallback"})
        return _new_session_response()

    @app.post("/logout")
    async def logout(request: Request):
        sessions.discard(request.cookies.get("admin_session", ""))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("admin_session")
        return resp

    @app.get("/", response_class=HTMLResponse)
    async def index(saved: int = 0, error: str = ""):
        env_vals = env_store.read()
        eff = dockerops.effective_env(RAG_CONTAINER)
        rows = []
        for s in SETTINGS:
            ev, fv = env_vals.get(s["key"]), eff.get(s["key"])
            rows.append({"spec": s, "env_value": ev, "effective_value": fv,
                         "dirty": ev is not None and ev != fv})
        summary = get(f"{monitoring_url}/v1/alerts/summary")
        smtp_enabled = (summary.get("json") or {}).get("smtp_enabled") if summary["ok"] else None
        ctx = {
            "settings_rows": rows,
            "job": job_manager.current(),
            "reports": list_reports(reports_dir),
            "rag_state": dockerops.container_state(RAG_CONTAINER),
            "monitoring_state": dockerops.container_state(MON_CONTAINER),
            "smtp_enabled": smtp_enabled,
            "saved": bool(saved), "error": error or None,
        }
        return HTMLResponse(render_admin_page(ctx))

    @app.post("/api/settings")
    async def save_settings(request: Request):
        form = dict(await request.form())
        updates = {k: v for k, v in form.items() if k in WHITELIST and str(v).strip() != ""}
        try:
            changes = env_store.apply(updates)
        except SettingError as e:
            return RedirectResponse(f"/?error={quote(str(e))}", status_code=303)
        for key, old, new in changes:
            job_manager.log_change({"kind": "setting", "key": key, "old": old, "new": new})
        return RedirectResponse("/?saved=1", status_code=303)

    @app.post("/api/restart")
    async def restart_rag():
        dockerops.restart(RAG_CONTAINER)
        job_manager.log_change({"kind": "restart", "container": RAG_CONTAINER})
        return {"ok": True}

    @app.get("/api/rag-health")
    async def rag_health():
        return {"ok": get(f"{rag_api_url}/health")["ok"]}

    @app.post("/api/test-connection")
    async def test_connection(request: Request):
        """重啟前先驗證推論後端端點通不通——測表單當前值（尚未存 .env），
        避免把打不通的位址寫進 .env 後重啟導致 rag-api 連不上模型。"""
        form = dict(await request.form())
        results: dict[str, dict] = {}
        for field, key in (("llm_base", "LLM_API_BASE"), ("embed_base", "EMBED_API_BASE")):
            raw = str(form.get(field, "")).strip()
            if not raw:
                continue
            try:
                base = validate(key, raw)
            except SettingError as e:
                results[key] = {"ok": False, "detail": f"格式錯誤：{e}"}
                continue
            res = probe(f"{base.rstrip('/')}/models")
            if not res["reachable"]:
                results[key] = {"ok": False,
                                "detail": f"連線失敗（{res['error']}）——重啟 rag-api 會連不上此端點"}
            elif res["status"] is not None and res["status"] >= 500:
                results[key] = {"ok": False, "detail": f"端點回應但異常（HTTP {res['status']}）"}
            else:
                results[key] = {"ok": True, "detail": f"可連線（HTTP {res['status']}）"}
        if not results:
            return {"ok": False, "results": {},
                    "message": "沒有可測試的端點：LLM_API_BASE / EMBED_API_BASE 皆為空"}
        return {"ok": all(v["ok"] for v in results.values()), "results": results}

    @app.post("/api/jobs/{name}")
    async def start_job(name: str, request: Request):
        if name not in JOB_CATALOG:
            return JSONResponse({"error": f"未知作業 {name}"}, status_code=404)
        form = dict(await request.form())
        cmd = _build_cmd(name, form, reports_dir)
        if cmd is None:
            return JSONResponse({"error": "參數不合法：報告檔不存在或含路徑分隔符"}, status_code=400)
        try:
            job = job_manager.start(name, JOB_CATALOG[name]["container"], cmd,
                                    meta={"form": {k: v for k, v in form.items()}})
        except JobBusy as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return job

    @app.get("/api/jobs/current")
    async def current_job():
        return job_manager.current() or {"state": "idle"}

    @app.get("/api/reports")
    async def reports():
        return list_reports(reports_dir)

    @app.get("/api/reports/compare")
    async def compare(base: str, cur: str):
        if not (_safe_report_name(reports_dir, base) and _safe_report_name(reports_dir, cur)):
            return JSONResponse({"error": "報告檔名不合法"}, status_code=400)
        return flip_compare(reports_dir / base, reports_dir / cur)

    @app.post("/api/alert-test")
    async def alert_test(request: Request, severity: str = "info"):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = dict(await request.form())
            severity = form.get("severity", severity)
        if severity not in ("info", "warning", "critical"):
            return JSONResponse({"error": "severity 只能是 info/warning/critical"}, status_code=400)
        r = post(f"{monitoring_url}/v1/alerts/test", params={"severity": severity})
        job_manager.log_change({"kind": "alert_test", "severity": severity, "ok": r["ok"]})
        return {"ok": r["ok"]}

    return app


def create_app_from_env() -> FastAPI:
    env_file = Path(os.environ.get("ENV_FILE", "/host_env/.env"))
    data_dir = Path(os.environ.get("ADMIN_DATA_DIR", "/app/data"))
    card_serials = {s.strip() for s in os.environ.get("ADMIN_CARD_SERIALS", "").split(",") if s.strip()}
    password_fallback = os.environ.get("ENABLE_PASSWORD_FALLBACK", "false").lower() in ("1", "true", "yes")
    admin_user = os.environ.get("ADMIN_USERNAME", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not card_serials and not password_fallback:
        raise RuntimeError("ADMIN_CARD_SERIALS 為空且 ENABLE_PASSWORD_FALLBACK 未開——無任何登入途徑（設定錯誤要大聲）")
    if password_fallback and (not admin_user or not admin_password):
        raise RuntimeError("啟用帳密 fallback 但 ADMIN_USERNAME/ADMIN_PASSWORD 未設定")
    ops = DockerOps()
    return create_app(
        EnvStore(env_file, data_dir / "env-backups"),
        JobManager(data_dir, ops.exec_stream),
        ops,
        Path(os.environ.get("REPORTS_DIR", "/mon_data/reports")),
        os.environ.get("MONITORING_URL", "http://monitoring:8200"),
        os.environ.get("RAG_API_URL", "http://rag-api:8000"),
        card_serials=card_serials,
        admin_user=admin_user,
        admin_password=admin_password,
        password_fallback=password_fallback,
    )


app = create_app_from_env() if os.environ.get("ADMIN_RUNTIME") == "1" else None
