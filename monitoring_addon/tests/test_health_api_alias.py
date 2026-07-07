import json

import service.app as appmod


def test_health_and_drift_alias_same_payload(tmp_path, monkeypatch):
    """/v1/drift is a byte-identical alias of /v1/health, but carries Deprecation header."""
    pytest_httpx = None
    try:
        from fastapi.testclient import TestClient
    except Exception:                      # pragma: no cover - env without httpx
        import pytest
        pytest.skip("fastapi TestClient (httpx) unavailable")

    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    (reports / "health_2026-06-26.json").write_text(
        json.dumps({"severity": "normal", "overall_score": 0}), encoding="utf-8")
    monkeypatch.setattr(appmod, "_REPORTS_DIR", reports, raising=False)
    monkeypatch.setenv("ALERT_LOOPS_DISABLED", "true")

    client = TestClient(appmod.app)
    h = client.get("/v1/health")
    d = client.get("/v1/drift")
    assert h.status_code == 200 and d.status_code == 200
    assert h.content == d.content                   # payload 位元組相同
    assert d.headers.get("Deprecation") == "true"   # 別名帶棄用標頭
