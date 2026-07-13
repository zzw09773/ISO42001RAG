import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from admincore.dockerops import DockerOps
from admincore.envstore import EnvStore
from admincore.jobs import JobManager
from service.app import create_app

from tests.test_dockerops import FakeClient
from tests.test_jobs import fake_runner_ok


@pytest.fixture()
def client(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOP_K=5\nLLM_API_KEY=zzz\n", encoding="utf-8")
    store = EnvStore(env, tmp_path / "backups")
    jm = JobManager(tmp_path / "data", fake_runner_ok)
    ops = DockerOps(client=FakeClient())
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "vv_report_a.json").write_text(json.dumps(
        {"generated_at": "2026-07-08", "hit_rate": 0.9,
         "per_query": [{"id": "q1", "query": "x", "hit_rate": 1.0}]}), encoding="utf-8")
    calls = {"post": [], "get": []}

    def fake_post(url, **kw):
        calls["post"].append((url, kw))
        return {"status_code": 200, "ok": True, "json": {}}

    def fake_get(url, **kw):
        calls["get"].append((url, kw))
        return {"status_code": 200, "ok": True, "json": {"smtp_enabled": False}}

    def fake_probe(url):
        calls.setdefault("probe", []).append(url)
        if ":9999" in url or "unreach" in url:
            return {"reachable": False, "status": None, "error": "ConnectError"}
        if "err500" in url:
            return {"reachable": True, "status": 503, "error": None}
        return {"reachable": True, "status": 200, "error": None}

    class FakeClaims:
        employee_id = "1090868"
        display_name = "測試卡"

    class FakeCardError(Exception):
        pass

    def fake_verify(signed_b64, expected_nonce):
        if signed_b64 == "BAD":
            raise FakeCardError("bad signature")
        if signed_b64 == "STRANGER":
            c2 = FakeClaims(); c2.employee_id = "9999999"
            return c2
        return FakeClaims()

    app = create_app(store, jm, ops, reports, "http://monitoring:8200",
                     "http://rag-api:8000",
                     card_serials={"1090868"},
                     admin_user="u", admin_password="p", password_fallback=True,
                     verify_card=fake_verify,
                     http_post=fake_post, http_get=fake_get, http_probe=fake_probe)
    c = TestClient(app, follow_redirects=False)
    c._jm, c._env, c._calls, c._tmp = jm, env, calls, tmp_path
    # 預設以測試假帳密登入（fallback 開啟；真帳密只存在 .env，絕不進測試碼）
    r = c.post("/login", data={"username": "u", "password": "p"})
    assert r.status_code == 303
    return c


def _card_login(fresh_client):
    tok = fresh_client.get("/api/auth/card/challenge").json()["challenge_token"]
    return fresh_client.post("/api/auth/card/verify",
                             data={"challenge_token": tok, "signed_data": "GOOD"})


def test_login_required(client):
    fresh = TestClient(client.app, follow_redirects=False)
    assert fresh.get("/").status_code == 303
    assert fresh.get("/").headers["location"] == "/login"
    assert fresh.get("/api/jobs/current").status_code == 401
    assert fresh.get("/login").status_code == 200


def test_health_is_public(client):
    fresh = TestClient(client.app, follow_redirects=False)
    response = fresh.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_card_login_success(client):
    fresh = TestClient(client.app, follow_redirects=False)
    r = _card_login(fresh)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert fresh.get("/api/jobs/current").status_code == 200
    log = (client._tmp / "data" / "changes.jsonl").read_text()
    assert '"card"' in log and '"1090868"' in log


def test_card_login_bad_signature(client):
    fresh = TestClient(client.app, follow_redirects=False)
    tok = fresh.get("/api/auth/card/challenge").json()["challenge_token"]
    r = fresh.post("/api/auth/card/verify",
                   data={"challenge_token": tok, "signed_data": "BAD"})
    assert r.status_code == 200 and "憑證卡驗證失敗" in r.text
    assert fresh.get("/api/jobs/current").status_code == 401


def test_card_login_not_whitelisted_shows_serial(client):
    fresh = TestClient(client.app, follow_redirects=False)
    tok = fresh.get("/api/auth/card/challenge").json()["challenge_token"]
    r = fresh.post("/api/auth/card/verify",
                   data={"challenge_token": tok, "signed_data": "STRANGER"})
    assert r.status_code == 200 and "9999999" in r.text and "ADMIN_CARD_SERIALS" in r.text
    assert fresh.get("/api/jobs/current").status_code == 401


def test_card_challenge_token_single_use(client):
    fresh = TestClient(client.app, follow_redirects=False)
    tok = fresh.get("/api/auth/card/challenge").json()["challenge_token"]
    assert fresh.post("/api/auth/card/verify",
                      data={"challenge_token": tok, "signed_data": "GOOD"}).status_code == 303
    fresh2 = TestClient(client.app, follow_redirects=False)
    r = fresh2.post("/api/auth/card/verify",
                    data={"challenge_token": tok, "signed_data": "GOOD"})
    assert r.status_code == 401   # token 一次性（反 replay）


def test_login_wrong_password(client):
    fresh = TestClient(client.app, follow_redirects=False)
    r = fresh.post("/login", data={"username": "u", "password": "wrong"})
    assert r.status_code == 200 and "帳號或密碼錯誤" in r.text
    assert fresh.get("/api/jobs/current").status_code == 401


def test_password_fallback_disabled(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOP_K=5\n", encoding="utf-8")
    app2 = create_app(EnvStore(env, tmp_path / "b"),
                      JobManager(tmp_path / "d", fake_runner_ok),
                      DockerOps(client=FakeClient()), tmp_path,
                      "http://monitoring:8200", "http://rag-api:8000",
                      card_serials={"1090868"},
                      admin_user="u", admin_password="p", password_fallback=False,
                      verify_card=lambda s, n: None,
                      http_post=lambda u, **k: {"ok": True, "status_code": 200, "json": {}},
                      http_get=lambda u, **k: {"ok": True, "status_code": 200, "json": {}})
    fresh = TestClient(app2, follow_redirects=False)
    assert fresh.post("/login", data={"username": "u", "password": "p"}).status_code == 403


def test_logout(client):
    r = client.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/api/jobs/current").status_code == 401


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "維運管理台" in r.text


def test_settings_save_and_change_log(client):
    r = client.post("/api/settings", data={"TOP_K": "8"})
    assert r.status_code == 303 and r.headers["location"] == "/?saved=1"
    assert "TOP_K=8" in client._env.read_text()
    log = (client._tmp / "data" / "changes.jsonl").read_text().splitlines()
    assert any('"TOP_K"' in l for l in log)


def test_settings_invalid_redirects_with_error(client):
    r = client.post("/api/settings", data={"TOP_K": "abc"})
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert "TOP_K=5" in client._env.read_text()   # 沒寫入


def test_job_start_and_busy(client):
    r = client.post("/api/jobs/online_vv")
    assert r.status_code == 200 and r.json()["state"] == "running"
    client._jm.wait()
    assert client.get("/api/jobs/current").json()["state"] == "done"


def test_job_bad_name_and_bad_param(client):
    assert client.post("/api/jobs/nope").status_code == 404
    r = client.post("/api/jobs/regression_gate",
                    data={"baseline": "../etc/passwd", "current": "vv_report_a.json", "tag": "t"})
    assert r.status_code == 400


def test_regression_gate_builds_container_paths(client):
    r = client.post("/api/jobs/regression_gate",
                    data={"baseline": "vv_report_a.json", "current": "vv_report_a.json", "tag": "t1"})
    assert r.status_code == 200
    cmd = r.json()["cmd"]
    assert "--baseline" in cmd and "/app/data/reports/vv_report_a.json" in cmd
    client._jm.wait()


def test_reports_and_compare(client):
    assert client.get("/api/reports").json()[0]["file"] == "vv_report_a.json"
    r = client.get("/api/reports/compare",
                   params={"base": "vv_report_a.json", "cur": "vv_report_a.json"})
    assert r.json()["newly_failed"] == []


def test_restart_and_alert_test(client):
    assert client.post("/api/restart").json()["ok"] is True
    assert client.post("/api/alert-test", params={"severity": "warning"}).json()["ok"] is True
    assert any("alerts/test" in u for u, _ in client._calls["post"])


def test_test_connection_reachable(client):
    r = client.post("/api/test-connection",
                    data={"llm_base": "http://gw:7000/v1", "embed_base": "http://embed:7100/v1"})
    j = r.json()
    assert j["ok"] is True
    assert j["results"]["LLM_API_BASE"]["ok"] and "可連線" in j["results"]["LLM_API_BASE"]["detail"]
    # 探測打的是 {base}/models
    assert any(u.endswith("/models") for u in client._calls["probe"])


def test_test_connection_unreachable(client):
    r = client.post("/api/test-connection", data={"llm_base": "http://gw:9999/v1"})
    j = r.json()
    assert j["ok"] is False
    assert "連線失敗" in j["results"]["LLM_API_BASE"]["detail"]


def test_test_connection_5xx_is_not_ok(client):
    r = client.post("/api/test-connection", data={"llm_base": "http://err500:7000/v1"})
    assert r.json()["results"]["LLM_API_BASE"]["ok"] is False


def test_test_connection_bad_format(client):
    r = client.post("/api/test-connection", data={"llm_base": "gw:7000/v1"})  # 無 scheme
    j = r.json()
    assert j["ok"] is False and "格式錯誤" in j["results"]["LLM_API_BASE"]["detail"]


def test_test_connection_empty(client):
    j = client.post("/api/test-connection", data={}).json()
    assert j["results"] == {} and j["ok"] is False


def test_test_connection_requires_auth(client):
    fresh = TestClient(client.app, follow_redirects=False)
    assert fresh.post("/api/test-connection", data={"llm_base": "http://x/v1"}).status_code == 401
