"""
Service Status HTML Renderer

Pure-Python; takes a payload dict (from dashboard_data.build_payload) and
returns a self-contained HTML string with inline SVG charts.

The dashboard exposes a 0-100 health gauge, per-dimension score bars, and
status-page style latency blocks. Each latency block is non-sticky: a recovered
latest bucket is shown as recovered while older abnormal buckets remain visible
as history.

No external JS dependencies (no Chart.js, no fonts from CDN). Everything is
inline so the resulting HTML can be:
  - opened offline,
  - emailed as an audit attachment,
  - printed to PDF,
  - served live by FastAPI.
"""
from __future__ import annotations

import math
from html import escape
from typing import Dict, List, Optional

try:  # keep render usable even if config import path shifts
    from .config import BUSINESS_GOAL_HIT_RATE, MIN_PERF_SAMPLE
except Exception:  # pragma: no cover
    BUSINESS_GOAL_HIT_RATE, MIN_PERF_SAMPLE = 0.90, 30

# 4-level severity scale shared with thresholds.classify_health.
# (score → level boundaries; kept in sync with thresholds.LEVEL_BOUNDARIES)
_SCORE_ZONES = [
    (0, 25, "#16a34a", "normal 正常"),
    (25, 50, "#2563eb", "watch 留意"),
    (50, 75, "#d97706", "warning 警示"),
    (75, 100, "#dc2626", "critical 嚴重"),
]


# ───────────── Inline SVG primitives ─────────────


def _line_chart(
    points: List[Optional[float]],
    labels: List[str],
    *,
    width: int = 720,
    height: int = 180,
    color: str = "#1e3a8a",
    fill_under: bool = True,
) -> str:
    """Draw a single-series line chart as inline SVG. None values create gaps."""
    if not points or all(p is None for p in points):
        # 與有資料分支相同的響應式屬性；固定 width 會把 grid 欄撐爆（no-data 時破版）
        return (
            f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMidYMid meet" '
            f'xmlns="http://www.w3.org/2000/svg" style="background:#fff">'
            f'<text x="20" y="{height // 2}" fill="#888">no data</text></svg>'
        )

    pad_l, pad_r, pad_t, pad_b = 44, 16, 14, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    valid = [p for p in points if p is not None]
    vmax = max(valid) if valid else 1.0
    vmin = min(valid) if valid else 0.0
    if vmax == vmin:
        vmax = vmin + 1.0

    def x_pos(i: int) -> float:
        return pad_l + (i * plot_w / max(len(points) - 1, 1))

    def y_pos(v: float) -> float:
        return pad_t + plot_h - (v - vmin) / (vmax - vmin) * plot_h

    # Build path; break on None
    path_parts: List[str] = []
    pen_down = False
    for i, p in enumerate(points):
        if p is None:
            pen_down = False
            continue
        cmd = "L" if pen_down else "M"
        path_parts.append(f"{cmd}{x_pos(i):.1f},{y_pos(p):.1f}")
        pen_down = True
    line_path = " ".join(path_parts)

    # Fill polygon under line (optional)
    fill_path = ""
    if fill_under and pen_down:
        # close back to baseline
        last_visible = max(i for i, p in enumerate(points) if p is not None)
        first_visible = min(i for i, p in enumerate(points) if p is not None)
        fill_path = (
            line_path
            + f" L{x_pos(last_visible):.1f},{pad_t + plot_h:.1f}"
            + f" L{x_pos(first_visible):.1f},{pad_t + plot_h:.1f} Z"
        )

    # Y-axis ticks: 3 levels
    yticks: List[str] = []
    for frac in (0.0, 0.5, 1.0):
        v = vmin + (vmax - vmin) * frac
        y = pad_t + plot_h - frac * plot_h
        yticks.append(
            f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y}" y2="{y}" stroke="#eef2f7"/>'
            f'<text x="{pad_l - 6}" y="{y + 3}" text-anchor="end" fill="#5b6578" font-size="10">{_fmt(v)}</text>'
        )

    # X-axis labels: show first, mid, last
    n = len(labels)
    xticks: List[str] = []
    if n > 0:
        for idx in {0, n // 2, n - 1}:
            xticks.append(
                f'<text x="{x_pos(idx):.1f}" y="{height - 8}" text-anchor="middle" fill="#5b6578" font-size="10">{escape(labels[idx])}</text>'
            )

    svg = (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" style="background:#fff">'
        + "".join(yticks)
        + (f'<path d="{fill_path}" fill="{color}" fill-opacity="0.10" stroke="none"/>' if fill_path else "")
        + f'<path d="{line_path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>'
        + "".join(xticks)
        + "</svg>"
    )
    return svg


def _bar_chart(
    items: List[Dict[str, object]],
    *,
    width: int = 720,
    height: int = 220,
    color: str = "#1e3a8a",
    label_key: str = "label",
    value_key: str = "count",
) -> str:
    if not items:
        return (
            f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMidYMid meet" '
            f'xmlns="http://www.w3.org/2000/svg" style="background:#fff">'
            f'<text x="20" y="{height // 2}" fill="#888">no data</text></svg>'
        )

    pad_l, pad_r, pad_t, pad_b = 100, 16, 14, 20
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    vmax = max(int(it[value_key]) for it in items) or 1
    bar_h = plot_h / len(items)

    rows: List[str] = []
    for i, it in enumerate(items):
        y = pad_t + i * bar_h
        v = int(it[value_key])
        bw = (v / vmax) * plot_w
        label = escape(str(it[label_key]))
        rows.append(
            f'<text x="{pad_l - 8}" y="{y + bar_h / 2 + 4}" text-anchor="end" fill="#1a1f2c" font-size="11">{label}</text>'
            f'<rect x="{pad_l}" y="{y + 2}" width="{bw:.1f}" height="{bar_h - 6:.1f}" fill="{color}" fill-opacity="0.85" rx="2"/>'
            f'<text x="{pad_l + bw + 6:.1f}" y="{y + bar_h / 2 + 4}" fill="#5b6578" font-size="11">{v}</text>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" style="background:#fff">'
        + "".join(rows)
        + "</svg>"
    )


def _fmt(v: float) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:.0f}"
    if abs(v) >= 10:
        return f"{v:.1f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


# ───────────── Main render ─────────────


_SEVERITY_TONE = {
    "normal": ("#ffffff", "#166534", "NORMAL 正常"),
    "warning": ("#ffffff", "#92400e", "WARNING 警示"),
    "critical": ("#ffffff", "#991b1b", "CRITICAL 嚴重"),
}

_GOAL_TONE = {
    "met":          ("#ffffff", "#166534", "目標達成 MET"),
    "not_met":      ("#ffffff", "#991b1b", "目標未達 NOT MET"),
    "inconclusive": ("#ffffff", "#92400e", "尚未驗證 INCONCLUSIVE"),
}

# 「一個整體狀態 + 三個維度」呈現所需色階
_DIM_TONE = {
    "ok":       ("#dcfce7", "#166534", "OK"),
    "watch":    ("#dbeafe", "#1e40af", "WATCH"),
    "warning":  ("#fef3c7", "#92400e", "WARNING"),
    "critical": ("#fee2e2", "#991b1b", "CRITICAL"),
}
_DIM_RANK = {"ok": 0, "watch": 1, "warning": 2, "critical": 3}

_STATUS_BIN_TONE = {
    "normal": ("#16a34a", "正常"),
    "watch": ("#2563eb", "留意"),
    "warning": ("#d97706", "警示"),
    "critical": ("#dc2626", "嚴重"),
    "no_data": ("#d1d5db", "無資料"),
}

_MARK_TONE = {
    "ok": "#16a34a", "watch": "#2563eb", "warning": "#d97706",
    "critical": "#dc2626", "none": "#9aa3b2",
}


def _status_mark(level: str, text: str) -> str:
    """報告書式狀態標記：RGB 色點 + 文字（黑白列印時語意由文字承載）。"""
    color = _MARK_TONE.get(level, _MARK_TONE["none"])
    return (
        f'<span class="mark" style="color:{color};">'
        f'<i class="dot" style="background:{color};"></i>{escape(text)}</span>'
    )


def _dim_status_A(kpi: dict, anomalies: list, alerts_recent: list) -> tuple:
    """A. 運作健康 — 只看 source ∈ {anomaly, integrity} 的告警 + KPI。

    drift 來源的告警屬於 C 維度，整合在 _dim_status_C；不應在 A 重複算。
    """
    sec_alerts = int(kpi.get("security_alerts", 0))
    anom_count = sum(int(a.get("count", 0)) for a in anomalies)
    a_sources = {"anomaly", "integrity"}
    a_alerts = [r for r in (alerts_recent or []) if r.get("source") in a_sources]
    n_crit = sum(1 for r in a_alerts if r.get("severity") == "critical")
    n_warn = sum(1 for r in a_alerts if r.get("severity") == "warning")
    if n_crit > 0:
        return "critical", [f"近 24h critical 運作告警 {n_crit} 件"]
    if n_warn > 0:
        return "warning", [f"近 24h warning 運作告警 {n_warn} 件"]
    if sec_alerts > 0:
        return "warning", [f"視窗內安全告警 {sec_alerts} 件"]
    if anom_count > 0:
        return "watch", [f"視窗內異常旗標 {anom_count} 次"]
    return "ok", ["無異常事件"]


def _dim_status_B(goal_status: str, goal_current, goal_target: float) -> tuple:
    """B. 品質保證 — 從業務目標 (Hit Rate) 推導。"""
    if goal_status == "met":
        cur = f"{goal_current:.4f}" if isinstance(goal_current, (int, float)) else "—"
        return "ok", [f"Hit Rate {cur} ≥ {goal_target}"]
    if goal_status == "not_met":
        cur = f"{goal_current:.4f}" if isinstance(goal_current, (int, float)) else "—"
        return "warning", [f"Hit Rate {cur} < {goal_target}"]
    return "watch", ["尚未驗證 — 無最新 V&V 報告"]


def _dim_status_C(health: dict) -> tuple:
    """C. 服務健康 — 直接取 health severity 對應。

    insufficient_data（樣本不足，無統計判定）顯示為 watch 而非 ok：
    「沒有資料」不等於「確認正常」，儀表板不應給綠燈。
    """
    sev = health.get("severity", "normal")
    mapping = {
        "normal": "ok",
        "watch": "watch",
        "warning": "warning",
        "critical": "critical",
        "insufficient_data": "watch",
    }
    level = mapping.get(sev, "ok")
    if sev == "insufficient_data":
        drivers = ["樣本不足，無健康判定"]
    else:
        drivers = [f"健康分數 {health.get('overall_score', 0)}/100"]
    reasons = health.get("severity_reasons") or []
    if reasons:
        drivers.append(reasons[0])
    return level, drivers


def _compute_overall(a_status, b_status, c_status) -> tuple:
    """整體狀態 = 三維度中最差者（worst-of-three）。"""
    pairs = [("A. 運作健康", a_status[0]), ("B. 品質保證", b_status[0]), ("C. 服務健康", c_status[0])]
    worst_dim, worst_lvl = max(pairs, key=lambda p: _DIM_RANK.get(p[1], 0))
    return worst_lvl, worst_dim


def _render_hero(level: str, worst_dim: str) -> str:
    _, fg, label = _DIM_TONE.get(level, _DIM_TONE["ok"])
    return (
        f'<div class="hero verdict">'
        f'  <div class="verdict-cap">整體健康狀態 · Overall Health</div>'
        f'  <div class="verdict-value" style="color:{fg};">'
        f'<i class="dot" style="background:{fg};"></i>{label}</div>'
        f'  <div class="verdict-note">最差子維度：<strong>{escape(worst_dim)}</strong>'
        f'（整體＝A/B/C 三維度最差值，不以平均稀釋異常）</div>'
        f'</div>'
    )


def _render_dim_strip(a_status, b_status, c_status) -> str:
    def _card(title: str, status: tuple) -> str:
        level, drivers = status
        _, fg, label = _DIM_TONE.get(level, _DIM_TONE["ok"])
        drv_lis = "".join(f"<li>{escape(d)}</li>" for d in drivers)
        return (
            f'<div class="dim-card">'
            f'  <div class="dim-title">{title}</div>'
            f'  <div class="dim-status" style="color:{fg};">'
            f'<i class="dot" style="background:{fg};"></i>{label}</div>'
            f'  <ul class="dim-drivers">{drv_lis}</ul>'
            f'</div>'
        )
    return (
        '<div class="dim-grid">'
        + _card("A · 運作健康", a_status)
        + _card("B · 品質保證", b_status)
        + _card("C · 服務健康", c_status)
        + '</div>'
    )


def _render_chapter_head(question: str, clauses: str, rows: list) -> str:
    """章頭固定對照表：觀察問題 / 對應條文 / 指標×門檻×實際×判定。

    rows: list of (指標, 門檻, 實際, (mark_level, mark_text))
    """
    body = "".join(
        f'<tr><td>{escape(m)}</td><td>{escape(t)}</td>'
        f'<td class="num">{escape(a)}</td><td>{_status_mark(lv, txt)}</td></tr>'
        for m, t, a, (lv, txt) in rows
    )
    return (
        '<table class="ch-head">'
        f'<tr class="ch-meta"><th>觀察問題</th><td colspan="3">{escape(question)}</td></tr>'
        f'<tr class="ch-meta"><th>對應條文</th><td colspan="3">{escape(clauses)}</td></tr>'
        '<tr><th>指標</th><th>門檻</th><th>實際</th><th>判定</th></tr>'
        + body + '</table>'
    )


def _render_alerts_banner(
    critical: int,
    warning: int,
    info: int,
    smtp_enabled: bool,
    *,
    current_health: str = "",
) -> str:
    """Top-of-page banner summarising last 24h alert volume + SMTP status."""
    recovered = critical > 0 and current_health in {"normal", "insufficient_data"}
    if critical > 0 and not recovered:
        cls = "critical"
        label = f"近 24 小時嚴重告警 {critical} 件"
    elif recovered:
        cls = "warning"
        label = f"近 24 小時歷史嚴重告警 {critical} 件；目前服務健康 {current_health.upper()}，已恢復"
    elif warning > 0:
        cls = "warning"
        label = f"近 24 小時警告告警 {warning} 件"
    else:
        cls = "calm"
        label = "近 24 小時無嚴重／警告告警"
    smtp_pill = '<span class="pill">SMTP ON</span>' if smtp_enabled else '<span class="pill">SMTP OFF</span>'
    return (
        f'<div class="alert-banner {cls}">'
        f'  {label}'
        f'  <span class="pill">CRITICAL {critical}</span>'
        f'  <span class="pill">WARNING {warning}</span>'
        f'  <span class="pill">INFO {info}</span>'
        f'  {smtp_pill}'
        f'</div>'
    )


def _render_alerts_table(alerts: list) -> str:
    """Render the recent-24h alerts table; empty state if none."""
    if not alerts:
        return '<div class="reasons">近 24 小時無告警記錄。</div>'
    rows = []
    for a in alerts[:50]:
        sev = a.get("severity", "info")
        ts = (a.get("timestamp") or "")[:19].replace("T", " ")
        rows.append(
            f"<tr>"
            f"<td class='ts'>{escape(ts)}</td>"
            f"<td class='sev-{escape(sev)}'>{escape(sev.upper())}</td>"
            f"<td><code>{escape(a.get('source', '?'))}</code></td>"
            f"<td><strong>{escape(a.get('title', ''))}</strong><br>{escape(a.get('message', ''))[:200]}</td>"
            f"</tr>"
        )
    return (
        '<table class="alerts-table"><thead>'
        '<tr><th>時間</th><th>等級</th><th>來源</th><th>訊息</th></tr>'
        '</thead><tbody>'
        + "".join(rows)
        + '</tbody></table>'
    )


def _render_status_bins(status_bins: dict) -> str:
    """Render status-page style hourly latency blocks."""
    if not status_bins:
        return ""
    bins = status_bins.get("bins") or []
    latest = status_bins.get("latest") or {}
    latest_status = latest.get("status", "no_data")
    latest_color, latest_label = _STATUS_BIN_TONE.get(latest_status, _STATUS_BIN_TONE["no_data"])
    latest_p95 = latest.get("p95_latency_ms")
    latest_text = (
        f"目前最近有資料時段：{latest_label}"
        + (f"（P95 {latest_p95} ms）" if latest_p95 is not None else "")
    )

    cells = []
    for b in bins:
        st = b.get("status", "no_data")
        color, label = _STATUS_BIN_TONE.get(st, _STATUS_BIN_TONE["no_data"])
        p95 = b.get("p95_latency_ms")
        title = (
            f"{b.get('label', '')} · {label} · "
            f"queries={b.get('queries', 0)} · "
            f"P95={p95 if p95 is not None else 'no data'}"
        )
        cells.append(
            f'<span class="status-cell" title="{escape(title)}" '
            f'style="background:{color};"></span>'
        )

    legend = "".join(
        f'<span><i style="background:{color}"></i>{label}</span>'
        for _, (color, label) in _STATUS_BIN_TONE.items()
    )
    return f"""
  <div class="status-history">
    <div class="status-history-head">
      <div>
        <div class="card-cap">近 24 小時延遲狀態格</div>
        <div class="status-current" style="color:{latest_color};">{escape(latest_text)}</div>
      </div>
      <div class="status-legend">{legend}</div>
    </div>
    <div class="status-grid">{''.join(cells)}</div>
    <div class="status-note">每格獨立判定該小時 P95 latency；最新有資料格恢復正常時，當前狀態即顯示恢復，舊異常格只作為歷史紀錄保留。</div>
  </div>
"""


# ───────────── Health score visualisations (gauge + per-dimension bars) ─────


def _gauge_point(cx: float, cy: float, r: float, score: float) -> tuple:
    """Polar→cartesian for a 180° top gauge: score 0 at 9 o'clock, 100 at 3."""
    angle = math.radians(180 - max(0.0, min(100.0, score)) * 1.8)
    return cx + r * math.cos(angle), cy - r * math.sin(angle)


def _render_drift_gauge(overall_score: float, severity: str) -> str:
    """Semicircular 0–100 gauge with the 4 severity zones and a needle.

    The headline visual of section C — turns the single overall health score
    (weakest-link across dimensions) into an at-a-glance dashboard gauge.
    """
    w, h, cx, cy, r = 320, 188, 160, 158, 122
    insufficient = severity == "insufficient_data"
    # Coloured zone arcs (sweep-flag 0 = over the top in y-down coords)
    arcs = []
    for lo, hi, color, _label in _SCORE_ZONES:
        x1, y1 = _gauge_point(cx, cy, r, lo)
        x2, y2 = _gauge_point(cx, cy, r, hi)
        arcs.append(
            f'<path d="M{x1:.1f},{y1:.1f} A{r},{r} 0 0 0 {x2:.1f},{y2:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="20" '
            f'stroke-opacity="{0.25 if insufficient else 0.9}" stroke-linecap="butt"/>'
        )
    # Tick labels at 0/25/50/75/100
    ticks = []
    for s in (0, 25, 50, 75, 100):
        tx, ty = _gauge_point(cx, cy, r + 18, s)
        ticks.append(
            f'<text x="{tx:.1f}" y="{ty + 4:.1f}" text-anchor="middle" '
            f'fill="#5b6578" font-size="11">{s}</text>'
        )
    # Needle + hub
    if insufficient:
        center_val = "—"
        center_sub = f"樣本不足 (n&lt;{MIN_PERF_SAMPLE})"
        needle = ""
    else:
        nx, ny = _gauge_point(cx, cy, r - 26, overall_score)
        needle = (
            f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
            f'stroke="#1a1f2c" stroke-width="3" stroke-linecap="round"/>'
            f'<circle cx="{cx}" cy="{cy}" r="6" fill="#1a1f2c"/>'
        )
        center_val = f"{overall_score:.0f}"
        center_sub = "/ 100 健康分數"
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:340px" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(arcs) + "".join(ticks) + needle
        + f'<text x="{cx}" y="{cy - 30}" text-anchor="middle" font-size="40" '
          f'font-weight="900" fill="#1a1f2c">{center_val}</text>'
        + f'<text x="{cx}" y="{cy - 10}" text-anchor="middle" font-size="12" '
          f'fill="#5b6578">{center_sub}</text>'
        + '</svg>'
    )


def _render_dim_score_bars(dimension_scores: dict) -> str:
    """Horizontal 0–100 bar per drift dimension over the 4 colour zones.

    Shows WHICH dimension drives the weakest-link overall score and by how much.
    """
    label_map = {
        "faithfulness": "幻覺 / 忠實度",
        "rejection": "拒絕率 (Δ)",
        "latency": "延遲 P95",
        "availability": "系統可用率",
        "security": "安全告警率",
    }
    if not dimension_scores:
        return '<div class="reasons">樣本不足或無資料，未產生維度分數。</div>'
    # zone background gradient (fixed stops at 25/50/75)
    zone_bg = (
        "linear-gradient(90deg,"
        "#16a34a 0%,#16a34a 25%,"
        "#2563eb 25%,#2563eb 50%,"
        "#d97706 50%,#d97706 75%,"
        "#dc2626 75%,#dc2626 100%)"
    )
    rows = []
    for key, score in sorted(dimension_scores.items(), key=lambda kv: -kv[1]):
        label = label_map.get(key, key)
        pct = max(0.0, min(100.0, float(score)))
        rows.append(
            f'<div class="score-row">'
            f'<div class="score-label">{escape(label)}</div>'
            f'<div class="score-track" style="background:{zone_bg};">'
            f'<div class="score-fillmask" style="left:{pct:.1f}%;"></div>'
            f'<div class="score-marker" style="left:{pct:.1f}%;"></div>'
            f'</div>'
            f'<div class="score-num">{score:.0f}</div>'
            f'</div>'
        )
    return '<div class="score-bars">' + "".join(rows) + "</div>"


def _render_health_methodology() -> str:
    """How each health metric is measured — surfaced on the dashboard itself
    so the committee never has to leave the page to ask 'how is this measured?'.
    Covers the 6 health dimensions: faithfulness, rejection, latency,
    availability, security, audit-chain integrity."""
    rows = [
        ("幻覺 / 忠實度", "Faithfulness 分數（RAGAS）",
         "回答內容對檢索文件的接地程度（絕對品質門檻 0.90）。",
         "營運單位最關注項；可獨立升級為 critical。"),
        ("拒絕率", "當期拒絕率 vs 基線之差值（Δ）",
         "每超出基線 +0.10 → +25 分；衡量模型拒答行為的異常程度。",
         "拒絕率急升代表安全過濾規則或查詢分佈可能出現問題。"),
        ("延遲 P95", "P95 回應延遲（ms）vs 基線",
         "當期 P95 延遲與基線的百分比偏差，依比例映射為 0–100 分。",
         "延遲突增通常先於用戶投訴，是早期預警指標。"),
        ("系統可用率", "recent_ok_pct（最近 3 次探測）",
         "健康燈號看最近探測；24h uptime 仍保留在下方表格作為歷史佐證。",
         "服務恢復後不再被舊故障卡死；任一關鍵依賴連續失敗仍觸發 hard-down。"),
        ("安全告警率", "視窗內安全事件 / 總查詢",
         "0.05→50 分、0.10→75 分、0.20→100 分（容忍度低）。",
         "安全面向不容稀釋：門檻刻意偏嚴。"),
        ("audit 鏈完整性", "audit log 雜湊鏈驗證（binary）",
         "逐筆驗證 prev_hash / hash 鏈，任一斷鏈即標記 broken。",
         "ISO 42001 A.8.3 要求日誌不可竄改；broken 立即升 critical。"),
    ]
    body = "".join(
        f"<tr><td><strong>{escape(d)}</strong></td><td>{escape(m)}</td>"
        f"<td>{escape(f)}</td><td>{escape(w)}</td></tr>"
        for d, m, f, w in rows
    )
    return (
        '<details class="method-panel" open><summary>健康指標如何計算？（點開看各指標的計算方式）</summary>'
        '<table class="method-table"><thead><tr>'
        '<th>維度</th><th>方法</th><th>計算方式</th><th>量什麼 / 為何這樣選</th>'
        '</tr></thead><tbody>' + body + '</tbody></table>'
        '<div class="method-note">每個維度各自映射為 0–100 分數（見下方「門檻為何這樣定義」），'
        '整體健康分數取所有維度的<strong>最大值（weakest-link，最弱環節）</strong>——'
        '只要任一面向異常，整體即升級，寧可誤報不可漏報。</div>'
        '</details>'
    )


def _render_threshold_rationale() -> str:
    """Why the thresholds are set where they are — the design justification,
    surfaced on the dashboard. Mirrors thresholds.py header."""
    level_rows = "".join(
        f'<tr><td><span class="zone-chip" style="background:{c}"></span>{lo}–{hi}</td>'
        f'<td>{escape(lbl)}</td></tr>'
        for lo, hi, c, lbl in _SCORE_ZONES
    )
    return (
        '<details class="method-panel" open><summary>門檻為何這樣定義？（點開看標準的設計依據）</summary>'

        '<div class="rationale-grid">'

        '<div class="rationale-box"><h4>① 四級分數刻度</h4>'
        '<table class="method-table"><thead><tr><th>分數</th><th>等級與行動</th></tr></thead>'
        f'<tbody>{level_rows}</tbody></table>'
        '<p class="method-note">0–25 正常波動不告警；25–50 記錄留意；50–75 通知人工調查；'
        '75–100 立即處理。</p></div>'

        '<div class="rationale-box"><h4>② 各維度錨點</h4>'
        '<ul class="method-list">'
        '<li><strong>幻覺 / Faithfulness</strong>：0.90→0、0.80→50、0.65→75 分（絕對門檻 0.90）</li>'
        '<li><strong>拒絕率</strong>：每超出基線 +0.10 → +25 分</li>'
        '<li><strong>安全告警率</strong>：0.05→50、0.10→75、0.20→100 分（容忍度低）</li>'
        '<li><strong>延遲 P95</strong>：依與基線偏差百分比線性映射</li>'
        '<li><strong>系統可用率</strong>：recent_ok_pct &lt; 99%→watch；&lt; 95%→critical；24h uptime 作為歷史佐證</li>'
        '<li><strong>audit 鏈完整性</strong>：intact→0 分；broken→100 分（二元）</li>'
        '</ul></div>'

        f'<div class="rationale-box"><h4>③ 最小樣本守門（n &lt; {MIN_PERF_SAMPLE}）</h4>'
        f'<p class="method-note">視窗內查詢數少於 <strong>{MIN_PERF_SAMPLE}</strong> 時，'
        '效能類指標（拒絕率、延遲、安全告警率）在小樣本下數值不穩定，可能產生假性高分。此時直接判定 '
        '<strong>insufficient_data</strong>，<u>不評分、不告警、也不給綠燈</u>'
        '（「沒資料」≠「正常」）。'
        '<br><strong>例外</strong>：Faithfulness 來自獨立 RAGAS 評估，不受此門檻，低流量仍可獨立升 critical。'
        '可用率與 audit 鏈完整性亦不受此限制（有探測即有判定）。</p></div>'

        '<div class="rationale-box"><h4>④ 整體 = 最弱環節</h4>'
        '<p class="method-note">整體健康分數取各維度<strong>最大值</strong>而非平均——'
        '任一面向異常即整體升級，避免被其他正常維度稀釋。</p></div>'

        '</div>'

        '<div class="method-note" style="margin-top:14px;">'
        '上述門檻為工程校準之預設值，<strong>最終風險容忍度待稽核負責人簽核</strong>。</div>'
        '</details>'
    )


def _faith_cell(health: dict) -> str:
    """Faithfulness 當期值 + 來源/新鮮度（judge 模型、報告日期、過期警示）。(P5)"""
    f = health.get("faithfulness", {}) or {}
    cur = f.get("current")
    if cur is None:
        return "尚未評估"
    meta = f.get("report_meta") or {}
    bits = [str(cur)]
    if meta.get("judge_model"):
        bits.append(f"judge={escape(str(meta['judge_model']))}")
    if meta.get("generated_at"):
        bits.append(f"報告 {escape(str(meta['generated_at'])[:10])}")
    if meta.get("stale"):
        age = meta.get("age_days")
        suffix = f"（{age} 天）" if age is not None else ""
        bits.append(f"<span style='color:#dc2626'>已過期{suffix}，請重跑 RAGAS</span>")
    return " · ".join(bits)


def _render_safety_controls(sc: dict) -> str:
    """防護守則觸發統計（對應 RAG/docs/SAFETY_CONTROLS.md 守則 ③④①）。

    ISO 42001 A.8/A.9「防線有在運作」的證據——數字高代表攻擊/離題被擋下，
    純顯示、不影響健康燈。
    """
    if not sc:
        return ""
    r3 = sc.get("rule3_input_sanitizer", {}) or {}
    r4 = sc.get("rule4_scope_reject", {}) or {}
    r1 = sc.get("rule1_auth_failure", {}) or {}

    def _rows(d: dict) -> str:
        if not d:
            return '<tr><td colspan="2" style="color:#16a34a;">視窗內無觸發</td></tr>'
        return "".join(
            f'<tr><td><code>{escape(str(k))}</code></td><td>{v}</td></tr>'
            for k, v in d.items()
        )

    return f"""
  <h3 id="appendix-4">附錄四 · 防護守則觸發（Safety Controls）</h3>
  <div class="dim-context">對應 <code>RAG/docs/SAFETY_CONTROLS.md</code> 守則 ③④①。「防線有在運作」的 ISO 42001 A.8 / A.9 證據——數字高代表攻擊或離題提問被擋下，<strong>不影響系統健康燈</strong>。</div>

  <div class="kpi-grid">
    <div class="kpi"><div class="label">③ Input Sanitizer 攔截</div><div class="val">{r3.get('total', 0)}</div></div>
    <div class="kpi"><div class="label">④ 範圍外婉拒</div><div class="val">{r4.get('total', 0)}</div></div>
    <div class="kpi"><div class="label">① 認證失敗</div><div class="val">{r1.get('total', 0)}</div></div>
  </div>

  <div class="grid-2" style="margin-top:14px;">
    <div class="card">
      <h3>③ Input Sanitizer — 依威脅類型（threat_type）</h3>
      <table><thead><tr><th>threat_type</th><th>次數</th></tr></thead><tbody>{_rows(r3.get('by_threat_type', {}))}</tbody></table>
    </div>
    <div class="card">
      <h3>④ Scope Classify — 婉拒原因（reason）</h3>
      <table><thead><tr><th>reason</th><th>次數</th></tr></thead><tbody>{_rows(r4.get('by_reason', {}))}</tbody></table>
    </div>
    <div class="card">
      <h3>① Authentication — 失敗原因（reason）</h3>
      <table><thead><tr><th>reason</th><th>次數</th></tr></thead><tbody>{_rows(r1.get('by_reason', {}))}</tbody></table>
    </div>
  </div>
"""


def render_dashboard(payload: dict) -> str:
    kpi = payload.get("kpi", {})
    daily = payload.get("daily_series", [])
    health = payload.get("health", {})
    perf = health.get("perf", {})
    vv_snap = (payload.get("vv") or {}).get("snapshot") or {}
    sev = health.get("severity", "normal")
    bg, fg, sev_label = _SEVERITY_TONE.get(sev, _SEVERITY_TONE["normal"])
    health_overall_score = health.get("overall_score", 0) or 0
    health_dim_scores = health.get("dimension_scores", {}) or {}
    status_bins = payload.get("status_bins") or {}

    # Business goal status (Hit Rate ≥ 0.90)
    goal = payload.get("business_goal", {})
    goal_status = goal.get("status", "inconclusive")
    goal_bg, goal_fg, goal_label = _GOAL_TONE.get(goal_status, _GOAL_TONE["inconclusive"])
    goal_target = goal.get("target", 0.90)
    goal_current = goal.get("current")
    goal_current_text = (
        f"{goal_current:.4f}" if isinstance(goal_current, (int, float)) else "—"
    )
    goal_reason = goal.get("reason", "")

    dates = [d["date"] for d in daily]
    queries_series = [d.get("queries") for d in daily]
    rej_rate_series = [d.get("rejection_rate") for d in daily]
    latency_series = [d.get("avg_latency_ms") for d in daily]

    anomalies = payload.get("anomalies", [])

    ret_metrics = vv_snap.get("retrieval", {})

    alerts_block = payload.get("alerts") or {}
    alerts_recent = alerts_block.get("recent", [])
    alerts_counts = alerts_block.get("counts_24h", {"info": 0, "warning": 0, "critical": 0})
    smtp_enabled = alerts_block.get("smtp_enabled", False)
    alerts_critical = int(alerts_counts.get("critical", 0))
    alerts_warning = int(alerts_counts.get("warning", 0))
    alerts_info = int(alerts_counts.get("info", 0))

    # availability and integrity cards
    avail_block = payload.get("availability") or {}
    uptime_pct = avail_block.get("uptime_pct")
    recent_ok_pct = avail_block.get("recent_ok_pct")
    recent_probes = avail_block.get("recent_probes")
    current_ok = avail_block.get("current_ok")
    current_at = avail_block.get("current_at")
    per_dep_uptime = avail_block.get("per_dep_uptime") or {}
    current_per_dep = avail_block.get("current_per_dep") or {}
    integrity_block = payload.get("integrity") or {}
    integrity_status = integrity_block.get("status") or "unknown"

    availability_rows = []
    for dep, pct in per_dep_uptime.items():
        dep_current = current_per_dep.get(dep) or {}
        dep_ok = dep_current.get("ok")
        dep_state = "OK" if dep_ok is True else "DOWN" if dep_ok is False else "UNKNOWN"
        availability_rows.append(
            f"<tr><td><code>{escape(str(dep))}</code></td>"
            f"<td>{dep_state}</td>"
            f"<td>{f'{pct:.1f}%' if pct is not None else '—'}</td></tr>"
        )
    availability_table = (
        '<table><thead><tr><th>依賴項</th><th>目前</th><th>24h 可用率</th></tr></thead><tbody>'
        + "".join(availability_rows)
        + '</tbody></table>'
    ) if availability_rows else '<div class="reasons">無個別依賴項可用率資料。</div>'

    # 1+3 narrative: compute the three dimension statuses and the single
    # worst-of-three overall status.
    a_status = _dim_status_A(kpi, anomalies, alerts_recent)
    b_status = _dim_status_B(goal_status, goal_current, goal_target)
    c_status = _dim_status_C(health)
    overall_level, worst_dim = _compute_overall(a_status, b_status, c_status)

    anom_count = sum(int(a.get("count", 0)) for a in anomalies)
    ch_a_head = _render_chapter_head(
        "每筆請求是否被正確處理？",
        "ISO 42001 A.6.2.4 / A.9.1 · ISO 27001 A.8.15",
        [
            ("安全告警", "0 件", f"{kpi.get('security_alerts', 0)} 件",
             ("ok", "PASS") if kpi.get("security_alerts", 0) == 0 else ("warning", "CHECK")),
            ("異常旗標", "0 次", f"{anom_count} 次",
             ("ok", "PASS") if anom_count == 0 else ("watch", "WATCH")),
            ("綜合判定（含近 24h 運作告警）", "無 warning/critical 告警",
             a_status[1][0] if a_status[1] else "—",
             (a_status[0], _DIM_TONE[a_status[0]][2])),
        ],
    )
    ch_b_head = _render_chapter_head(
        "檢索是否找到對的條文？生成是否引用正確？是否幻覺？",
        "ISO 42001 A.4 / A.7",
        [
            ("Hit Rate（v1.0.0 唯一 gating 指標）", f"≥ {goal_target}", goal_current_text,
             (b_status[0], _DIM_TONE[b_status[0]][2])),
        ],
    )
    ch_c_head = _render_chapter_head(
        "服務是否健康、可用，且結果可信？",
        "ISO 42001 A.6.2.5（變更管理）/ A.8.3（稽核日誌）",
        [
            ("健康分數（weakest-link）", "< 25 為正常區", f"{health_overall_score}/100",
             (c_status[0], _DIM_TONE[c_status[0]][2])),
            ("audit 鏈完整性", "intact", integrity_status,
             ({"intact": "ok", "broken": "critical"}.get(integrity_status, "watch"),
              integrity_status.upper())),
        ],
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>ISO 42001 Service Status — {escape(payload.get('generated_at', '')[:10])}</title>
<style>
  :root {{
    --ink:#1a1f2c; --muted:#5b6578; --line:#c9d1dc; --hairline:#e5eaf1;
    --paper:#fff; --soft:#f6f8fc; --accent:#1e3a8a;
    --mono:"JetBrains Mono","Consolas","Courier New",monospace;
  }}
  *,*::before,*::after {{ box-sizing:border-box; }}
  body {{ font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;
         margin:0; background:var(--soft); color:var(--ink); font-size:15px; line-height:1.7; }}
  .report {{ max-width:1080px; margin:0 auto; padding:40px 48px 56px; background:var(--paper);
            border-left:1px solid var(--line); border-right:1px solid var(--line); min-height:100vh; }}
  h1 {{ font-size:24px; font-weight:900; margin:0; letter-spacing:.01em; }}
  .title-en {{ font-size:13px; font-weight:500; color:var(--muted); margin-left:10px; }}
  .live-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; background:#16a34a;
              margin-left:10px; vertical-align:middle; animation:livepulse 2s ease-in-out infinite; }}
  @keyframes livepulse {{ 0%,100%{{opacity:1;}} 50%{{opacity:0.25;}} }}
  .refresh-info {{ font-size:11px; font-weight:500; color:var(--muted); margin-left:6px; vertical-align:middle; }}
  .report-rule {{ border:0; border-top:2px solid var(--ink); margin:14px 0 0; }}
  .report-meta {{ width:100%; border-collapse:collapse; font-size:13px; margin:0 0 8px; }}
  .report-meta th {{ text-align:left; font-weight:700; color:var(--muted); padding:8px 14px 8px 0;
                    border-bottom:1px solid var(--hairline); white-space:nowrap; width:1%; }}
  .report-meta td {{ padding:8px 24px 8px 0; border-bottom:1px solid var(--hairline); }}
  .num {{ font-family:var(--mono); font-variant-numeric:tabular-nums; }}
  .toc {{ border:1px solid var(--line); padding:12px 18px; margin:18px 0 8px;
         font-size:13px; display:flex; flex-wrap:wrap; gap:6px 20px; align-items:baseline; }}
  .toc-cap {{ font-weight:900; letter-spacing:.08em; color:var(--muted); font-size:11px; }}
  .toc a {{ color:var(--accent); text-decoration:none; font-weight:700; }}
  .toc a:hover {{ text-decoration:underline; }}
  section {{ margin-top:34px; }}
  h2 {{ font-size:18px; font-weight:900; margin:0 0 4px; padding-bottom:6px;
       border-bottom:1px solid var(--ink); }}
  h2 .ch-no {{ color:var(--accent); margin-right:8px; }}
  h2 .en {{ font-size:12px; font-weight:500; color:var(--muted); margin-left:8px; }}
  h3 {{ font-size:14px; font-weight:800; margin:20px 0 8px; }}
  .mark {{ font-weight:800; white-space:nowrap; }}
  .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; vertical-align:baseline; }}
  .verdict-row {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:14px 0; }}
  .verdict {{ border:1px solid var(--ink); padding:18px 22px; }}
  .verdict-cap {{ font-size:11px; letter-spacing:.1em; font-weight:700; color:var(--muted); margin-bottom:8px; }}
  .verdict-value {{ font-size:30px; font-weight:900; line-height:1.15; }}
  .verdict-value .dot {{ width:13px; height:13px; margin-right:10px; }}
  .verdict-note {{ font-size:13px; color:#3b4252; margin-top:10px; }}
  .verdict-reason {{ font-size:12px; color:var(--muted); margin-top:6px; }}
  .dim-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin:14px 0 6px; }}
  .dim-card {{ border-top:2px solid var(--ink); padding:10px 2px 0; }}
  .dim-title {{ font-size:12px; color:var(--muted); font-weight:700; margin-bottom:6px; letter-spacing:.04em; }}
  .dim-status {{ font-size:20px; font-weight:900; line-height:1; }}
  .dim-drivers {{ margin:10px 0 0; padding-left:18px; font-size:12.5px; color:#3b4252; }}
  .dim-drivers li {{ margin:1px 0; }}
  .dim-context {{ font-size:12.5px; color:var(--muted); margin:4px 0 14px; }}
  .ch-head {{ width:100%; border-collapse:collapse; font-size:13px; margin:10px 0 18px; }}
  .ch-head th {{ text-align:left; font-weight:700; padding:7px 12px 7px 0; white-space:nowrap;
                border-bottom:1px solid var(--hairline); color:var(--muted); }}
  .ch-head tr:nth-child(3) th {{ color:var(--ink); border-top:2px solid var(--ink);
                                 border-bottom:1px solid var(--ink); }}
  .ch-head td {{ padding:7px 12px 7px 0; border-bottom:1px solid var(--hairline); }}
  .ch-head .ch-meta th {{ width:1%; padding-right:18px; }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:8px; }}
  .kpi {{ border:1px solid var(--line); padding:12px 16px; background:var(--paper); }}
  .kpi .label {{ font-size:11px; color:var(--muted); letter-spacing:0.08em; }}
  .kpi .val {{ font-size:26px; font-weight:800; margin-top:4px; font-family:var(--mono); font-variant-numeric:tabular-nums; }}
  .kpi.danger .val {{ color:#991b1b; }}
  .kpi.warn .val {{ color:#92400e; }}
  .severity-banner {{ display:inline-block; padding:5px 12px; font-weight:800; font-size:13px;
                      border:1px solid var(--line); background:{bg}; color:{fg}; }}
  .warmup-banner {{ margin:12px 0; padding:11px 16px; font-size:12.5px; line-height:1.7;
                    background:var(--soft); border:1px solid var(--line); color:#3b4252; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin:8px 0 16px; }}
  th {{ text-align:left; padding:7px 10px; font-size:12px; font-weight:700;
       border-top:2px solid var(--ink); border-bottom:1px solid var(--ink); }}
  td {{ padding:6px 10px; border-bottom:1px solid var(--hairline); vertical-align:top; }}
  tr:nth-child(even) td {{ background:#fafbfd; }}
  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  .card {{ border:1px solid var(--line); padding:14px 16px; min-width:0; }}
  .card svg {{ max-width:100%; }}
  .card h3 {{ margin:0 0 8px; font-size:12px; color:var(--muted); letter-spacing:0.06em; }}
  .reasons {{ background:var(--soft); border:1px solid var(--hairline); padding:10px 14px;
             margin-top:6px; font-size:13px; color:#3b4252; }}
  .reasons li {{ margin:2px 0; }}
  code {{ font-family:var(--mono); font-size:.9em; background:#eef2f7; padding:.1em .35em; }}
  .footer {{ margin-top:36px; padding-top:14px; border-top:2px solid var(--ink);
            font-size:11px; color:var(--muted); }}
  @media (max-width:780px) {{ .verdict-row,.dim-grid,.grid-2 {{ grid-template-columns:1fr; }} }}
  /* Alerts（JS 契約：class 名與 pill 文字格式不可變） */
  .alert-banner {{ display:flex; gap:14px; align-items:center; padding:12px 16px;
                   border:1px solid var(--line); margin:14px 0 6px; font-size:13.5px; font-weight:700; }}
  .alert-banner.critical {{ color:#991b1b; border-color:#991b1b; }}
  .alert-banner.warning {{ color:#92400e; border-color:#92400e; }}
  .alert-banner.calm {{ color:#166534; }}
  .alert-banner .pill {{ display:inline-block; padding:2px 10px; font-size:11px; font-weight:800;
                          background:var(--soft); border:1px solid var(--hairline); }}
  .alerts-table td {{ font-size:12.5px; vertical-align:top; }}
  .alerts-table .sev-critical {{ color:#991b1b; font-weight:700; }}
  .alerts-table .sev-warning {{ color:#92400e; font-weight:700; }}
  .alerts-table .sev-info {{ color:var(--muted); }}
  .alerts-table .ts {{ font-family:var(--mono); font-size:11.5px; color:var(--muted); white-space:nowrap; }}
  /* 健康分數：儀表 + 維度分數帶（zone 色帶為資料視覺編碼，保留） */
  .drift-overview {{ display:grid; grid-template-columns:340px 1fr; gap:22px; align-items:center;
                     margin:14px 0 6px; padding:16px 18px; border:1px solid var(--line); }}
  .card-cap {{ font-size:11px; letter-spacing:0.07em; color:var(--muted); font-weight:700; margin-bottom:8px; }}
  .drift-gauge-box {{ text-align:center; }}
  .score-bars {{ display:flex; flex-direction:column; gap:9px; }}
  .score-row {{ display:grid; grid-template-columns:128px 1fr 34px; align-items:center; gap:10px; }}
  .score-label {{ font-size:12px; color:#3b4252; }}
  .score-track {{ position:relative; height:14px; overflow:hidden;
                  box-shadow:inset 0 0 0 1px rgba(0,0,0,0.06); }}
  .score-fillmask {{ position:absolute; top:0; bottom:0; right:0; background:rgba(255,255,255,0.74); }}
  .score-marker {{ position:absolute; top:-3px; bottom:-3px; width:3px; transform:translateX(-1.5px);
                   background:var(--ink); }}
  .score-num {{ font-size:13px; font-weight:800; text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; }}
  .zone-legend {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:12px; font-size:11px; color:var(--muted); }}
  .zone-legend span {{ display:inline-flex; align-items:center; gap:5px; }}
  .zone-legend i {{ width:11px; height:11px; display:inline-block; }}
  @media (max-width:780px) {{ .drift-overview {{ grid-template-columns:1fr; }} }}
  /* 附錄：方法論 / 門檻依據面板 */
  .method-panel {{ border:1px solid var(--line); margin:12px 0; background:var(--paper); overflow:hidden; }}
  .method-panel > summary {{ cursor:pointer; padding:12px 16px; font-weight:800; font-size:13.5px;
                             color:var(--ink); background:var(--soft); list-style:none;
                             display:flex; align-items:center; gap:8px; }}
  .method-panel > summary::before {{ content:"▸"; transition:transform 0.15s; font-size:12px; color:var(--accent); }}
  .method-panel[open] > summary::before {{ transform:rotate(90deg); }}
  .method-panel > summary::-webkit-details-marker {{ display:none; }}
  .method-table {{ width:100%; border-collapse:collapse; font-size:12.5px; margin:0; }}
  .method-table th {{ background:none; color:var(--ink); padding:7px 12px; font-size:11.5px; }}
  .method-table td {{ padding:7px 12px; border-bottom:1px solid var(--hairline); vertical-align:top; }}
  .method-note {{ font-size:12px; color:#4b5568; padding:10px 16px; line-height:1.6; }}
  .method-list {{ font-size:12.5px; color:#3b4252; margin:6px 0; padding-left:20px; line-height:1.7; }}
  .rationale-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; padding:14px 16px; }}
  .rationale-box {{ border:1px solid var(--hairline); padding:10px 12px; }}
  .rationale-box h4 {{ margin:0 0 8px; font-size:12.5px; color:var(--accent); }}
  .rationale-box .method-table td, .rationale-box .method-table th {{ padding:4px 8px; }}
  .rationale-box .method-note {{ padding:8px 0 0; }}
  .zone-chip {{ display:inline-block; width:11px; height:11px; margin-right:6px; vertical-align:-1px; }}
  @media (max-width:780px) {{ .rationale-grid {{ grid-template-columns:1fr; }} }}
  /* 近 24 小時延遲狀態格（使用者指定保留樣式） */
  .status-history {{ border:1px solid var(--line); padding:14px 16px; margin:14px 0; background:var(--paper); }}
  .status-history-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:10px; }}
  .status-current {{ font-size:14px; font-weight:800; }}
  .status-grid {{ display:grid; grid-template-columns:repeat(24,1fr); gap:4px; align-items:center; }}
  .status-cell {{ display:block; height:26px; border-radius:2px; box-shadow:inset 0 0 0 1px rgba(0,0,0,0.08); }}
  .status-legend {{ display:flex; flex-wrap:wrap; gap:10px; font-size:11px; color:var(--muted); justify-content:flex-end; }}
  .status-legend span {{ display:inline-flex; align-items:center; gap:5px; }}
  .status-legend i {{ width:10px; height:10px; display:inline-block; }}
  .status-note {{ margin-top:10px; font-size:12px; color:var(--muted); line-height:1.6; }}
  @media (max-width:780px) {{ .status-history-head {{ display:block; }} .status-grid {{ grid-template-columns:repeat(12,1fr); }} .status-legend {{ justify-content:flex-start; margin-top:8px; }} }}
  @media print {{
    body {{ background:#fff; }}
    .report {{ border:0; padding:14px; max-width:none; }}
    .live-dot, .refresh-info, #sse-status {{ display:none; }}
    .dot, .status-cell, .zone-legend i, .status-legend i, .zone-chip, .score-track
      {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    section {{ break-inside:avoid-page; }}
    .method-panel[open] > summary::before {{ content:""; }}
  }}
</style>
</head>
<body>
<div class="report">
  <header>
    <h1>ISO 42001 服務狀態報告<span class="title-en">Service Status Report</span>
      <span id="live-dot" class="live-dot" title="自動更新中"></span><span id="refresh-info" class="refresh-info"></span></h1>
    <hr class="report-rule">
  </header>
  <nav class="toc"><span class="toc-cap">目錄</span>
    <a href="#exec">執行摘要</a>
    <a href="#ch-a">A 運作健康</a>
    <a href="#ch-b">B 品質保證</a>
    <a href="#ch-c">C 服務健康</a>
    <a href="#ch-d">D 告警</a>
    <a href="#appendix">附錄</a>
    <a href="audit">稽核日誌搜尋 →</a>
  </nav>
  <div id="live-content">
  <table class="report-meta">
    <tr><th>產生時間</th><td class="num">{escape(payload.get('generated_at', ''))}</td>
        <th>資料視窗</th><td class="num">{payload.get('window_days', 0)} 天</td></tr>
    <tr><th>稽核日誌檔</th><td class="num">{payload.get('files_loaded', 0)} 個</td>
        <th>audit 鏈完整性</th><td>{_status_mark({'intact': 'ok', 'broken': 'critical'}.get(integrity_status, 'watch'), integrity_status.upper())}</td></tr>
  </table>

  <section id="exec">
    <h2>執行摘要<span class="en">Executive Summary</span></h2>
    <div class="verdict-row">
      {_render_hero(overall_level, worst_dim)}
      <div class="verdict goal-verdict">
        <div class="verdict-cap">業務目標 · Business Goal（v1.0.0 唯一 gating 指標）</div>
        <div class="verdict-value" style="color:{goal_fg};"><i class="dot" style="background:{goal_fg};"></i>{goal_label}</div>
        <div class="verdict-note">目標 Hit Rate ≥ {goal_target} · 當前 <strong class="num">{goal_current_text}</strong></div>
        <div class="verdict-reason">{escape(goal_reason)}</div>
      </div>
    </div>
    {_render_dim_strip(a_status, b_status, c_status)}
  </section>

  <section id="ch-a">
    <h2><span class="ch-no">A</span>運作健康<span class="en">Operational Health</span></h2>
    {ch_a_head}
    <div class="kpi-grid">
      <div class="kpi"><div class="label">總查詢數</div><div class="val">{kpi.get('queries', 0)}</div></div>
      <div class="kpi"><div class="label">拒絕數</div><div class="val">{kpi.get('rejections', 0)}</div></div>
      <div class="kpi"><div class="label">拒絕率</div><div class="val">{kpi.get('rejection_rate', 0):.2%}</div></div>
      <div class="kpi{' danger' if kpi.get('security_alerts', 0) > 0 else ''}"><div class="label">安全告警</div><div class="val">{kpi.get('security_alerts', 0)}</div></div>
      <div class="kpi{' warn' if kpi.get('anomalies', 0) > 0 else ''}"><div class="label">異常事件</div><div class="val">{kpi.get('anomalies', 0)}</div></div>
      <div class="kpi"><div class="label">P95 延遲 (ms)</div><div class="val">{kpi.get('p95_latency_ms') or '—'}</div></div>
    </div>
    <div class="grid-2" style="margin-top:14px;">
      <div class="card">
        <h3>每日查詢數</h3>
        {_line_chart(queries_series, dates)}
      </div>
      <div class="card">
        <h3>每日拒絕率</h3>
        {_line_chart(rej_rate_series, dates, color="#b45309")}
      </div>
      <div class="card">
        <h3>每日平均延遲 (ms)</h3>
        {_line_chart(latency_series, dates, color="#0891b2")}
      </div>
      <div class="card">
        <h3>異常旗標彙總</h3>
        {"<table><thead><tr><th>旗標</th><th>次數</th></tr></thead><tbody>" + "".join(f"<tr><td><code>{escape(a['flag'])}</code></td><td>{a['count']}</td></tr>" for a in anomalies) + "</tbody></table>" if anomalies else '<div class="reasons">視窗內無異常旗標。</div>'}
      </div>
    </div>
  </section>

  <section id="ch-b">
    <h2><span class="ch-no">B</span>品質保證<span class="en">Output Quality</span></h2>
    {ch_b_head}
    <div class="verdict-reason" style="margin:-8px 0 14px;">{escape(goal_reason)}</div>
    <h3>V&amp;V 基線快照</h3>
    {"<table><thead><tr><th>指標</th><th>分數</th></tr></thead><tbody>" + "".join(f"<tr><td>{k}</td><td class='num'>{v}</td></tr>" for k, v in ret_metrics.items()) + "</tbody></table>" if ret_metrics else '<div class="reasons">尚未載入 V&amp;V 報告。請執行 <code>python3 scripts/run_extended_vv.py</code> 或於 <code>../RAG/data/reports/</code> 提供 vv_report_*.json。</div>'}
  </section>

  <section id="ch-c">
    <h2><span class="ch-no">C</span>服務健康<span class="en">Service Health</span></h2>
    {ch_c_head}
    <span class="severity-banner">健康嚴重度：{sev_label}</span>
    {_render_status_bins(status_bins)}
    <div class="drift-overview">
      <div class="drift-gauge-box">
        <div class="card-cap">整體健康分數</div>
        {_render_drift_gauge(health_overall_score, sev)}
      </div>
      <div class="drift-bars-box">
        <div class="card-cap">各維度分數（0–100，取最大值為整體）</div>
        {_render_dim_score_bars(health_dim_scores)}
        <div class="zone-legend">
          <span><i style="background:#16a34a"></i>0–25 正常</span>
          <span><i style="background:#2563eb"></i>25–50 留意</span>
          <span><i style="background:#d97706"></i>50–75 警示</span>
          <span><i style="background:#dc2626"></i>75–100 嚴重</span>
        </div>
      </div>
    </div>
    <div class="grid-2" style="margin-top:14px;">
      <div class="card">
        <h3>系統可用率</h3>
        <div class="num" style="font-size:28px;font-weight:900;margin-bottom:6px;">
          {f"{recent_ok_pct:.1f}%" if recent_ok_pct is not None else "—"}
        </div>
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px;">
          最近 {recent_probes or 0} 次探針 · 目前 {'OK' if current_ok is True else 'DOWN' if current_ok is False else 'UNKNOWN'}
          {f" · {escape(str(current_at))[:19].replace('T', ' ')}" if current_at else ""}
        </div>
        {availability_table}
        <div style="font-size:12px;color:var(--muted);margin-top:8px;">
          24h uptime：{f"{uptime_pct:.1f}%" if uptime_pct is not None else "—"}；歷史故障保留於表格與告警紀錄，不阻塞目前恢復判定。
        </div>
      </div>
      <div class="card">
        <h3>audit 鏈完整性</h3>
        <div style="font-size:22px;font-weight:900;margin-bottom:6px;">
          {_status_mark({'intact': 'ok', 'broken': 'critical'}.get(integrity_status, 'watch'), integrity_status.upper())}
        </div>
        <div style="font-size:12px;color:var(--muted);">audit 鏈完整性（hash-chain 驗證，binary）</div>
      </div>
    </div>
    <div class="reasons" style="margin-top:10px;">
      <strong>判定理由：</strong>
      <ul>{"".join(f"<li>{escape(r)}</li>" for r in health.get('severity_reasons', []))}</ul>
    </div>
  </section>

  <section id="ch-d">
    <h2><span class="ch-no">D</span>告警（近 24 小時）<span class="en">Alerts</span></h2>
    <div style="font-size:12.5px;color:var(--muted);margin:4px 0 10px;">由 A/B/C 三維度共用之告警渠道（alerts.jsonl + 可選 SMTP）。告警 sink 詳見 <code>monitoring/alerting.py</code>。</div>
    {_render_alerts_banner(alerts_critical, alerts_warning, alerts_info, smtp_enabled, current_health=sev)}
    {_render_alerts_table(alerts_recent)}
  </section>

  <section id="appendix">
    <h2>附錄<span class="en">Appendix — 方法論、門檻依據、原始量測、防護守則</span></h2>
    <h3 id="appendix-1">附錄一 · 健康指標計算方法</h3>
    {_render_health_methodology()}
    <h3 id="appendix-2">附錄二 · 門檻設計依據</h3>
    {_render_threshold_rationale()}
    <h3 id="appendix-3">附錄三 · 原始量測值</h3>
    <table>
      <thead><tr><th>類別</th><th>指標</th><th>基線</th><th>當期</th><th>變動 / 評估</th></tr></thead>
      <tbody>
        <tr><td rowspan="4">Performance</td><td>拒絕率</td><td class="num">{perf.get('rejection_rate_baseline', 0)}</td><td class="num">{perf.get('rejection_rate_current', 0)}</td><td class="num">{perf.get('rejection_rate_delta', 0):+.4f}</td></tr>
        <tr><td>引用率</td><td class="num">{('尚無 V&amp;V 基線' if not perf.get('citation_rate_baseline') else perf.get('citation_rate_baseline'))}</td><td class="num">{perf.get('citation_rate_current', 0)}</td><td class="num">{('—' if not perf.get('citation_rate_baseline') else f"{perf.get('citation_rate_delta', 0):+.4f}")}</td></tr>
        <tr><td>平均延遲 (ms)</td><td class="num">{perf.get('avg_latency_baseline_ms') or '—'}</td><td class="num">{perf.get('avg_latency_current_ms') or '—'}</td><td class="num">{(str(perf.get('avg_latency_delta_pct')) + ' pct') if perf.get('avg_latency_delta_pct') is not None else '—'}</td></tr>
        <tr><td>安全告警率</td><td class="num">—</td><td class="num">{perf.get('security_alert_rate_current', 0)}</td><td class="num">—</td></tr>
        <tr><td>Faithfulness（忠實度）</td><td class="num">{health.get('faithfulness', {}).get('target', 0.90)}</td><td>{_faith_cell(health)}</td><td>{'執行 run_ragas_evaluation.py 後顯示（已接入儀表板）' if health.get('faithfulness', {}).get('current') is None else '&lt;0.80 嚴重（答案脫離條文）'}</td></tr>
      </tbody>
    </table>
    {_render_safety_controls(payload.get("safety_controls") or {})}
  </section>
  </div><!-- /live-content：自動更新時整段重抓替換 -->

  <div class="footer">
    Service status dashboard · audit log dir: <code>{escape(payload.get('audit_dir', ''))}</code>
    · <span id="sse-status">SSE 連線中...</span>
  </div>
</div>

<script>
/* v3.1 — 資料區自動更新（dynamic dashboard）。
   每 REFRESH_MS 重抓 /dashboard，原地替換 #live-content（重用伺服器渲染，
   不重寫前端 SVG/圖表邏輯）。分頁隱藏時暫停以省伺服器負載。
   告警仍由下方 SSE 即時推送，兩者獨立。 */
(function() {{
  var REFRESH_MS = 30000;
  var info = document.getElementById('refresh-info');
  function stamp(ok) {{
    if (!info) return;
    var t = new Date().toTimeString().slice(0, 8);
    info.textContent = ok ? ('每30秒自動更新 · ' + t) : ('更新失敗，重試中 · ' + t);
    info.style.color = ok ? '' : '#991b1b';
  }}
  function refresh() {{
    if (document.hidden) return;
    fetch('/dashboard', {{ cache: 'no-store' }})
      .then(function(r) {{ return r.text(); }})
      .then(function(html) {{
        // DOMParser 文件為惰性：不執行 script、不載入資源。以 importNode + 節點搬移
        // 取代 innerHTML，與既有 SSE 的 XSS-safe 模式一致（不重新解析 HTML 字串）。
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var fresh = doc.getElementById('live-content');
        var cur = document.getElementById('live-content');
        if (fresh && cur) {{
          var imported = document.importNode(fresh, true);
          cur.replaceChildren.apply(cur, Array.prototype.slice.call(imported.childNodes));
        }}
        stamp(true);
      }})
      .catch(function() {{ stamp(false); }});
  }}
  setInterval(refresh, REFRESH_MS);
  document.addEventListener('visibilitychange', function() {{ if (!document.hidden) refresh(); }});
  stamp(true);
}})();

(function() {{
  const SEV_BG = {{ critical:'#fef2f2', warning:'#fffbeb', info:'#f0fdf4', ok:'#f0fdf4' }};

  function el(tag, opts) {{
    const e = document.createElement(tag);
    if (!opts) return e;
    if (opts.cls)  e.className = opts.cls;
    if (opts.text != null) e.textContent = opts.text;
    if (opts.style) for (const k in opts.style) e.style[k] = opts.style[k];
    return e;
  }}

  function setStatus(text, ok) {{
    const x = document.getElementById('sse-status');
    if (!x) return;
    x.textContent = text;
    x.style.color = ok ? '#166534' : '#991b1b';
  }}

  function bumpBannerPill(sev) {{
    document.querySelectorAll('.alert-banner .pill').forEach(p => {{
      const m = p.textContent.match(/^(CRITICAL|WARNING|INFO)\\s+(\\d+)$/);
      if (m && m[1].toLowerCase() === sev) {{
        p.textContent = m[1] + ' ' + (parseInt(m[2]) + 1);
      }}
    }});
    const banner = document.querySelector('.alert-banner');
    if (!banner) return;
    if (sev === 'critical') {{
      banner.classList.remove('calm','warning'); banner.classList.add('critical');
    }} else if (sev === 'warning' && !banner.classList.contains('critical')) {{
      banner.classList.remove('calm'); banner.classList.add('warning');
    }}
  }}

  function flashHero(sev) {{
    const hero = document.querySelector('.hero');
    if (!hero) return;
    hero.style.transition = 'box-shadow 0.3s';
    const ring = sev === 'critical' ? '0 0 0 6px rgba(220,38,38,0.35)'
               : sev === 'warning'  ? '0 0 0 6px rgba(217,119,6,0.30)'
               :                       '0 0 0 6px rgba(22,163,74,0.25)';
    hero.style.boxShadow = ring;
    setTimeout(() => {{ hero.style.boxShadow = ''; }}, 1800);
  }}

  function buildRow(alert) {{
    const sev = alert.severity || 'info';
    const ts = (alert.timestamp || '').slice(0, 19).replace('T', ' ');
    const msg = (alert.message || '').slice(0, 200);
    const tr = el('tr', {{ style: {{ background: SEV_BG[sev] || '' }} }});
    tr.appendChild(el('td', {{ cls: 'ts', text: ts }}));
    tr.appendChild(el('td', {{ cls: 'sev-' + sev, text: sev.toUpperCase() }}));
    const tdSrc = el('td');
    tdSrc.appendChild(el('code', {{ text: alert.source || '?' }}));
    tr.appendChild(tdSrc);
    const tdMsg = el('td');
    tdMsg.appendChild(el('strong', {{ text: alert.title || '' }}));
    tdMsg.appendChild(document.createElement('br'));
    tdMsg.appendChild(document.createTextNode(msg));
    tr.appendChild(tdMsg);
    return tr;
  }}

  function prependAlertRow(alert) {{
    const tbody = document.querySelector('.alerts-table tbody');
    const tr = buildRow(alert);
    if (!tbody) return; // empty-state div is server-rendered; full reload covers first alert case
    tbody.insertBefore(tr, tbody.firstChild);
    setTimeout(() => {{
      tr.style.transition = 'background 0.6s';
      tr.style.background = '';
    }}, 1500);
  }}

  let es = null;
  let retryMs = 1000;

  function connect() {{
    try {{ if (es) es.close(); }} catch(e) {{}}
    es = new EventSource('/v1/alerts/stream');

    es.addEventListener('hello', (e) => {{
      setStatus('SSE 即時連線', true);
      retryMs = 1000;
    }});

    es.addEventListener('alert', (e) => {{
      try {{
        const a = JSON.parse(e.data);
        prependAlertRow(a);
        bumpBannerPill(a.severity);
        flashHero(a.severity);
      }} catch(err) {{
        console.error('bad alert frame', err);
      }}
    }});

    es.onerror = () => {{
      setStatus('SSE 中斷，重連中...', false);
      try {{ es.close(); }} catch(e) {{}}
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 15000);
    }};
  }}

  if (typeof EventSource === 'undefined') {{
    setStatus('SSE 不支援（瀏覽器過舊）', false);
  }} else {{
    connect();
  }}
}})();
</script>
</body>
</html>"""
    return html
