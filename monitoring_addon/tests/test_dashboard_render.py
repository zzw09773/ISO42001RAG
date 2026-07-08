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
        "EventSource('/v1/alerts/stream')",
    ):
        assert frag in html, f"missing JS contract selector: {frag}"


def test_render_integrity_status_none_safe():
    p = copy.deepcopy(_PAYLOAD)
    p["integrity"] = {"status": None}
    html = render_dashboard(p)   # must not raise
    assert "UNKNOWN" in html
