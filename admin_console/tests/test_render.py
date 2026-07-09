import re

from admincore.envstore import SETTINGS
from admincore.render import render_admin_page

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]")


def _ctx(**over):
    rows = [{"spec": s, "env_value": "5" if s["key"] == "TOP_K" else None,
             "effective_value": "3" if s["key"] == "TOP_K" else None,
             "dirty": s["key"] == "TOP_K"} for s in SETTINGS]
    ctx = {"settings_rows": rows, "job": None, "reports": [],
           "rag_state": "running", "monitoring_state": "running",
           "smtp_enabled": False, "saved": False, "error": None}
    ctx.update(over)
    return ctx


def test_page_structure_and_no_emoji():
    html = render_admin_page(_ctx())
    for frag in ("維運管理台", 'id="settings"', 'id="ops"', 'id="reports"',
                 'id="alerts-ops"', "已寫入，待重啟", "TOP_K",
                 'action="api/settings"', 'id="job-panel"',
                 'id="compare-result"', "SMTP：<b>關閉</b>"):
        assert frag in html, f"missing: {frag}"
    assert not _EMOJI_RE.search(html)
    assert "LLM_API_KEY" not in html          # 金鑰絕不出現


def test_running_job_rendered():
    html = render_admin_page(_ctx(job={"name": "ragas", "state": "running",
                                       "started_at": "t", "tail": ["a", "b"]}))
    assert "ragas" in html and "執行中" in html


def test_error_banner():
    html = render_admin_page(_ctx(error="TOP_K 需為整數"))
    assert "TOP_K 需為整數" in html


def test_login_page_card_primary():
    from admincore.render import render_login_page
    html = render_login_page()
    assert "插卡登入" in html and "api/auth/card/challenge" in html
    assert 'name="password"' not in html            # fallback 預設不渲染
    assert not _EMOJI_RE.search(html)


def test_login_page_with_fallback_and_error():
    from admincore.render import render_login_page
    html = render_login_page(password_fallback=True, error="帳號或密碼錯誤")
    assert 'name="username"' in html and 'name="password"' in html
    assert "帳號或密碼錯誤" in html


def test_settings_has_test_connection_button():
    from admincore.render import render_admin_page
    html = render_admin_page(_ctx())
    assert "測試連線" in html
    assert "api/test-connection" in html
    assert 'id="conn-test-result"' in html
