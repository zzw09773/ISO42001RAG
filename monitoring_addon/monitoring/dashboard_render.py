"""
Dashboard HTML Renderer

Pure-Python; takes a payload dict (from dashboard_data.build_payload) and
returns a self-contained HTML string with inline SVG charts.

No external JS dependencies (no Chart.js, no fonts from CDN). Everything is
inline so the resulting HTML can be:
  - opened offline,
  - emailed as an audit attachment,
  - printed to PDF,
  - served live by FastAPI.
"""
from __future__ import annotations

from html import escape
from typing import Dict, List, Optional


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
        return f'<svg width="{width}" height="{height}"><text x="20" y="{height // 2}" fill="#888">no data</text></svg>'

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
        return f'<svg width="{width}" height="{height}"><text x="20" y="{height // 2}" fill="#888">no data</text></svg>'

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
    "normal": ("#dcfce7", "#166534", "✅ NORMAL"),
    "warning": ("#fef3c7", "#92400e", "⚠️ WARNING"),
    "critical": ("#fee2e2", "#991b1b", "❌ CRITICAL"),
}

_GOAL_TONE = {
    "met":          ("#dcfce7", "#166534", "✅ 目標達成"),
    "not_met":      ("#fee2e2", "#991b1b", "❌ 目標未達"),
    "inconclusive": ("#fef3c7", "#92400e", "⚠️ 尚未驗證"),
}


def render_dashboard(payload: dict) -> str:
    kpi = payload.get("kpi", {})
    daily = payload.get("daily_series", [])
    drift = payload.get("drift", {})
    perf = drift.get("perf", {})
    data = drift.get("data", {})
    emb = drift.get("embedding", {})
    vv_snap = (payload.get("vv") or {}).get("snapshot") or {}
    sev = drift.get("severity", "normal")
    bg, fg, sev_label = _SEVERITY_TONE.get(sev, _SEVERITY_TONE["normal"])

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

    article_dist = payload.get("article_distribution", [])
    length_hist = payload.get("length_histogram", [])
    anomalies = payload.get("anomalies", [])

    ret_metrics = vv_snap.get("retrieval", {})

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>ISO 42001 Monitoring Dashboard — {escape(payload.get('generated_at', '')[:10])}</title>
<style>
  :root {{
    --c-text:#1a1f2c; --c-muted:#5b6578; --c-border:#d9dee6;
    --c-bg:#fff; --c-bg-soft:#f6f8fc; --c-accent:#1e3a8a;
  }}
  *,*::before,*::after {{ box-sizing:border-box; }}
  body {{ font-family:"Noto Sans TC","Inter",-apple-system,sans-serif;
         margin:0; background:var(--c-bg-soft); color:var(--c-text); }}
  .page {{ max-width:1180px; margin:0 auto; padding:32px 36px; background:var(--c-bg); border-left:1px solid var(--c-border); border-right:1px solid var(--c-border); }}
  h1 {{ font-size:24px; font-weight:900; margin:0 0 4px; }}
  .sub {{ color:var(--c-muted); font-size:13px; margin-bottom:24px; }}
  h2 {{ font-size:17px; font-weight:800; color:var(--c-accent); margin:28px 0 12px; padding-bottom:6px; border-bottom:2px solid var(--c-accent); }}
  .kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:8px; }}
  .kpi {{ background:var(--c-bg-soft); border:1px solid var(--c-border); border-radius:6px; padding:14px 16px; }}
  .kpi .label {{ font-size:11px; color:var(--c-muted); letter-spacing:0.08em; text-transform:uppercase; }}
  .kpi .val {{ font-size:26px; font-weight:800; margin-top:4px; }}
  .kpi.danger .val {{ color:#991b1b; }}
  .kpi.warn .val {{ color:#92400e; }}
  .severity-banner {{ display:inline-block; padding:6px 14px; border-radius:4px; font-weight:700; font-size:13px;
                      background:{bg}; color:{fg}; border:1px solid {fg}33; }}
  .goal-card {{ padding:18px 22px; border-radius:6px; margin:6px 0 4px; }}
  .goal-card .goal-title {{ font-size:18px; font-weight:800; margin-bottom:6px; }}
  .goal-card .goal-target,
  .goal-card .goal-current {{ font-size:14px; margin:2px 0; }}
  .goal-card .goal-reason {{ font-size:12px; color:#5b6578; margin-top:8px; font-style:italic; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin:8px 0 16px; }}
  th {{ background:var(--c-accent); color:#fff; text-align:left; padding:7px 10px; font-size:12px; }}
  td {{ padding:6px 10px; border-bottom:1px solid var(--c-border); vertical-align:top; }}
  tr:nth-child(even) td {{ background:var(--c-bg-soft); }}
  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  .card {{ border:1px solid var(--c-border); border-radius:6px; padding:14px 16px; }}
  .card h3 {{ margin:0 0 8px; font-size:13px; color:var(--c-muted); letter-spacing:0.06em; text-transform:uppercase; }}
  .reasons {{ background:#fafbfd; border:1px dashed var(--c-border); padding:10px 14px; margin-top:6px; font-size:13px; color:#3b4252; }}
  .reasons li {{ margin:2px 0; }}
  .footer {{ margin-top:32px; padding-top:16px; border-top:1px solid var(--c-border); font-size:11px; color:var(--c-muted); text-align:center; }}
  @media print {{ body {{ background:#fff; }} .page {{ border:0; padding:14px; }} }}
</style>
</head>
<body>
<div class="page">
  <h1>ISO 42001 Monitoring Dashboard</h1>
  <div class="sub">產生時間 {escape(payload.get('generated_at', ''))} · 視窗 {payload.get('window_days', 0)} 天 · 載入 {payload.get('files_loaded', 0)} 個稽核日誌檔</div>

  <span class="severity-banner">漂移嚴重度：{sev_label}</span>

  <h2>業務目標</h2>
  <div class="goal-card" style="background:{goal_bg}; border:1px solid {goal_fg}33; border-left:6px solid {goal_fg};">
    <div class="goal-title" style="color:{goal_fg};">{goal_label}</div>
    <div class="goal-target">目標：<strong>Hit Rate ≥ {goal_target}</strong></div>
    <div class="goal-current">當前：<strong style="color:{goal_fg};">Hit Rate = {goal_current_text}</strong></div>
    <div class="goal-reason">{escape(goal_reason)}</div>
  </div>

  <h2>關鍵指標 (KPI)</h2>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">總查詢數</div><div class="val">{kpi.get('queries', 0)}</div></div>
    <div class="kpi"><div class="label">拒絕數</div><div class="val">{kpi.get('rejections', 0)}</div></div>
    <div class="kpi"><div class="label">拒絕率</div><div class="val">{kpi.get('rejection_rate', 0):.2%}</div></div>
    <div class="kpi{' danger' if kpi.get('security_alerts', 0) > 0 else ''}"><div class="label">安全告警</div><div class="val">{kpi.get('security_alerts', 0)}</div></div>
    <div class="kpi{' warn' if kpi.get('anomalies', 0) > 0 else ''}"><div class="label">異常事件</div><div class="val">{kpi.get('anomalies', 0)}</div></div>
    <div class="kpi"><div class="label">P95 延遲 (ms)</div><div class="val">{kpi.get('p95_latency_ms') or '—'}</div></div>
  </div>

  <h2>時序趨勢</h2>
  <div class="grid-2">
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
      <h3>查詢長度分佈</h3>
      {_bar_chart(length_hist)}
    </div>
  </div>

  <h2>條文頻率 Top-15</h2>
  <div class="card">
    {_bar_chart(article_dist)}
  </div>

  <h2>漂移監測（與 V&amp;V 黃金資料集基線比較）</h2>
  <div class="reasons">
    <strong>判定理由：</strong>
    <ul>{"".join(f"<li>{escape(r)}</li>" for r in drift.get('severity_reasons', []))}</ul>
  </div>

  <table>
    <thead><tr><th>類別</th><th>指標</th><th>基線</th><th>當期</th><th>變動 / 評估</th></tr></thead>
    <tbody>
      <tr><td rowspan="4">Performance</td><td>拒絕率</td><td>{perf.get('rejection_rate_baseline', 0)}</td><td>{perf.get('rejection_rate_current', 0)}</td><td>{perf.get('rejection_rate_delta', 0):+.4f}</td></tr>
      <tr><td>引用率</td><td>{perf.get('citation_rate_baseline', 0)}</td><td>{perf.get('citation_rate_current', 0)}</td><td>{perf.get('citation_rate_delta', 0):+.4f}</td></tr>
      <tr><td>平均延遲 (ms)</td><td>{perf.get('avg_latency_baseline_ms') or '—'}</td><td>{perf.get('avg_latency_current_ms') or '—'}</td><td>{(str(perf.get('avg_latency_delta_pct')) + ' pct') if perf.get('avg_latency_delta_pct') is not None else '—'}</td></tr>
      <tr><td>安全告警率</td><td>—</td><td>{perf.get('security_alert_rate_current', 0)}</td><td>—</td></tr>
      <tr><td rowspan="3">Data</td><td>查詢長度 PSI</td><td>0.0</td><td>{data.get('query_length_psi', 0)}</td><td>PSI &gt; 0.25 嚴重</td></tr>
      <tr><td>條文頻率 PSI</td><td>0.0</td><td>{data.get('article_freq_psi', 0)}</td><td>同上</td></tr>
      <tr><td>字元 unigram KL</td><td>0.0</td><td>{data.get('char_unigram_kl', 0)}</td><td>越大越異常</td></tr>
      <tr><td rowspan="2">Embedding</td><td>後端</td><td colspan="2">{escape(emb.get('backend', 'unavailable'))} (samples={emb.get('samples', 0)})</td><td>—</td></tr>
      <tr><td>PC1 投影 PSI</td><td>0.0</td><td>{emb.get('pca_first_component_psi', 0)}</td><td>PSI &gt; 0.25 嚴重</td></tr>
    </tbody>
  </table>

  <h2>V&amp;V 基線快照</h2>
  {"<table><thead><tr><th>指標</th><th>分數</th></tr></thead><tbody>" + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in ret_metrics.items()) + "</tbody></table>" if ret_metrics else '<div class="reasons">尚未載入 V&amp;V 報告。請執行 <code>python3 scripts/run_extended_vv.py</code> 或於 <code>../RAG/data/reports/</code> 提供 vv_report_*.json。</div>'}

  <h2>異常旗標彙總</h2>
  {"<table><thead><tr><th>旗標</th><th>次數</th></tr></thead><tbody>" + "".join(f"<tr><td><code>{escape(a['flag'])}</code></td><td>{a['count']}</td></tr>" for a in anomalies) + "</tbody></table>" if anomalies else '<div class="reasons">視窗內無異常旗標。</div>'}

  <div class="footer">
    Generated by <code>monitoring_addon</code> · do NOT modify <code>RAG/</code> · audit log dir: <code>{escape(payload.get('audit_dir', ''))}</code>
  </div>
</div>
</body>
</html>"""
    return html
