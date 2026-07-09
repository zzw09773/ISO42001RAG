import copy
import re

from monitoring.dashboard_render import render_dashboard

_PAYLOAD = {
    "generated_at": "2026-06-26T00:00:00Z", "window_days": 30,
    "business_goal": {"status": "met", "current": 0.95, "target": 0.9, "metric": "hit_rate"},
    "kpi": {"queries": 100, "rejections": 5, "rejection_rate": 0.05,
            "security_alerts": 0, "anomalies": 0, "p95_latency_ms": 30000,
            "drift_severity": "normal"},
    "daily_series": [], "anomalies": [],
    "status_bins": {
        "kind": "latency_hourly",
        "hours": 2,
        "latest": {"status": "normal", "p95_latency_ms": 1200, "queries": 1, "label": "07/07 10:00"},
        "bins": [
            {"status": "critical", "p95_latency_ms": 65000, "queries": 1, "label": "07/07 09:00"},
            {"status": "normal", "p95_latency_ms": 1200, "queries": 1, "label": "07/07 10:00"},
        ],
    },
    "vv": {"available": False, "snapshot": {}},
    "health": {"severity": "normal", "overall_score": 10,
               "dimension_scores": {"faithfulness": 0, "rejection": 5, "latency": 18,
                                    "availability": 0, "security": 0},
               "severity_reasons": [], "perf": {},
               "faithfulness": {"current": 0.95, "target": 0.9},
               "availability": {"uptime_pct": 100.0}, "last_integrity_status": "intact"},
    "availability": {"uptime_pct": 100.0, "per_dep_uptime": {"rag-api": 100.0}, "hard_down": False},
    "integrity": {"status": "intact"},
    "alerts": {"recent": [], "counts_24h": {}, "smtp_enabled": False},
}


def test_render_has_health_cards_no_psi():
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    assert "服務健康" in html
    assert "可用率" in html
    assert "近 24 小時延遲狀態格" in html
    assert "目前最近有資料時段：正常" in html
    assert "PSI" not in html and "JSD" not in html and "語意" not in html


def test_render_handles_missing_optional():
    p = copy.deepcopy(_PAYLOAD)
    p["health"]["dimension_scores"] = {}
    render_dashboard(p)   # must not raise


def test_render_safety_controls_block():
    p = copy.deepcopy(_PAYLOAD)
    p["safety_controls"] = {
        "rule3_input_sanitizer": {"rule": "③", "total": 2, "by_threat_type": {"prompt_injection": 2}},
        "rule4_scope_reject": {"rule": "④", "total": 1, "by_reason": {"out_of_scope": 1}},
        "rule1_auth_failure": {"rule": "①", "total": 0, "by_reason": {}},
    }
    html = render_dashboard(p)
    assert "防護守則觸發" in html
    assert "prompt_injection" in html
    assert "SAFETY_CONTROLS.md" in html


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]"
)


def test_render_no_emoji():
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    m = _EMOJI_RE.search(html)
    assert not m, f"emoji found in render output: {m.group(0)!r}"


def test_render_report_structure():
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    for frag in (
        'id="exec"', 'id="ch-a"', 'id="ch-b"', 'id="ch-c"',
        'id="ch-d"', 'id="appendix"',
        'href="#exec"', 'href="#ch-a"', 'href="#ch-b"', 'href="#ch-c"',
        'href="#ch-d"', 'href="#appendix"',
        '執行摘要', '附錄',
    ):
        assert frag in html, f"missing report fragment: {frag}"


def test_render_js_contract_selectors():
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    for frag in (
        'id="live-content"', 'id="refresh-info"', 'id="sse-status"',
        'id="live-dot"', 'class="hero', 'alert-banner', 'pill',
        "EventSource('v1/alerts/stream')",
        "fetch('dashboard'",
    ):
        assert frag in html, f"missing JS contract selector: {frag}"
    # 絕對路徑在反代前綴 /monitoring/ 下會打到別的服務（v3.2 修正，禁止回歸）
    assert "EventSource('/v1" not in html
    assert "fetch('/dashboard'" not in html


def test_render_integrity_status_none_safe():
    p = copy.deepcopy(_PAYLOAD)
    p["integrity"] = {"status": None}
    html = render_dashboard(p)   # must not raise
    assert "UNKNOWN" in html


def test_render_no_data_charts_are_responsive():
    p = copy.deepcopy(_PAYLOAD)
    # 有日期但單一序列全為 None → 該圖走 no-data 分支
    p["daily_series"] = [
        {"date": "07/01", "queries": 3, "rejection_rate": 0.0, "avg_latency_ms": None},
        {"date": "07/02", "queries": 5, "rejection_rate": 0.0, "avg_latency_ms": None},
    ]
    html = render_dashboard(p)
    assert 'no data' in html
    # 固定 width 的 no-data SVG 會撐爆 grid（破版迴歸防護）
    assert '<svg width="' not in html


def test_render_empty_window_collapses_charts():
    p = copy.deepcopy(_PAYLOAD)
    p["daily_series"] = []          # 視窗完全無資料 → 四張趨勢卡收合為一行
    html = render_dashboard(p)
    assert "視窗內尚無查詢資料" in html
    assert "<h3>每日查詢數</h3>" not in html   # 空卡不再渲染


def test_render_risk_score_naming():
    # 分數語意為 severity（越高越差），顯示名稱不得再叫「健康分數」造成反向誤讀
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    assert "健康分數" not in html
    assert "風險分數" in html


def test_render_strips_emoji_from_data_strings():
    # severity_reasons 與歷史 alerts.jsonl 可能殘留 emoji，渲染端必須清除
    p = copy.deepcopy(_PAYLOAD)
    p["health"]["severity_reasons"] = ["🔴 可用率 hard-down → critical"]
    p["alerts"]["recent"] = [{
        "timestamp": "2026-07-08T13:20:32+08:00", "severity": "critical",
        "source": "availability", "title": "⚠️ system availability down",
        "message": "🔴 關鍵依賴連續 3 次探測失敗",
    }]
    html = render_dashboard(p)
    assert not _EMOJI_RE.search(html)
    assert "可用率 hard-down" in html and "system availability down" in html


def test_render_generated_at_local_time():
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    # 2026-06-26T00:00:00Z → UTC+8 顯示 08:00:00 並標註時區
    assert "2026-06-26 08:00:00（UTC+8）" in html


def test_render_alert_rows_layered():
    p = copy.deepcopy(_PAYLOAD)
    p["alerts"]["recent"] = [{
        "timestamp": "2026-07-08T13:20:32+08:00", "severity": "critical",
        "source": "availability", "title": "system availability down",
        "message": "關鍵依賴連續 3 次探測失敗：['embed-proxy']（/ready 失敗可能為 Triton 後端掛掉）",
    }]
    html = render_dashboard(p)
    assert 'class="src-pill"' in html          # 來源標籤
    assert 'class="alert-main"' in html        # 主訊息
    assert 'class="alert-aux"' in html         # 輔助說明
    assert "system availability down" in html


def test_faith_eval_reflects_value():
    from monitoring.dashboard_render import _faith_eval
    assert "良好" in _faith_eval({"faithfulness": {"current": 0.96}})
    assert "留意" in _faith_eval({"faithfulness": {"current": 0.85}})
    assert "嚴重" in _faith_eval({"faithfulness": {"current": 0.7}})
    assert "尚未評估" in _faith_eval({"faithfulness": {"current": None}})
    # 高分不得再出現「<0.80 嚴重」誤導字樣
    p = copy.deepcopy(_PAYLOAD)
    p["health"]["faithfulness"] = {"current": 0.9611, "target": 0.9}
    html = render_dashboard(p)
    assert "0.80 嚴重" not in html


def test_line_chart_single_point_marker():
    from monitoring.dashboard_render import _line_chart
    svg = _line_chart([None, 42.0], ["07/08", "07/09"])
    assert "<circle" in svg and "僅單日資料" in svg
    assert "<path" not in svg          # 不畫退化折線
    assert "07/09" in svg              # 標出是哪一天


def test_line_chart_multi_point_draws_line():
    from monitoring.dashboard_render import _line_chart
    assert "<path" in _line_chart([10.0, 20.0, 30.0], ["a", "b", "c"])
