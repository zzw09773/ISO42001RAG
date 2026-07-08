# 前端「印刷報告書」視覺系統重構 — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將監控儀表板（/dashboard）、稽核日誌搜尋（/audit）、md2html 文件模板與 INDEX 入口重構為統一的「印刷報告書」視覺系統（規格：`docs/superpowers/specs/2026-07-08-frontend-report-redesign-design.md`）。

**Architecture:** 純 render 層重構——資料層（`dashboard_data.py`）、判定函式（`_dim_status_A/B/C`、`_compute_overall`）、SVG 圖表函式、路由與查詢參數全部不動。儀表板改為單頁分層報告書（報告頭 → 錨點目錄 → 執行摘要 → A/B/C/D 章 → 附錄），既有 30 秒自動更新與 SSE JS 原封保留。

**Tech Stack:** Python f-string HTML 模板、內嵌 CSS（無 CDN）、pytest、Playwright MCP（目視驗證）。

> **實作偏離記錄（2026-07-08 執行完畢，最終審查 Ready to merge）：**
> 1. `_render_hero` 輸出 `class="hero verdict"`（計畫片段為 `verdict hero`）——計畫自身的 JS 契約測試斷言 `'class="hero'`，此順序同時滿足測試與 flashHero 選擇器。
> 2. `EXCLUDE_DIRS` 除計畫的 `"superpowers"` 外另加 `".superpowers"`（SDD 工作目錄含 .md，會被誤轉）。
> 3. 計畫 CSS 註解「資料語意」與既有禁字測試衝突，實作改用「資料視覺編碼」。
> 4. 計畫 CSS 漏列 `_render_safety_controls` 依賴的 `.dim-context` 規則，實作補回（非斜體版）；移除無元素引用的 `.goal-card` 規則。
> 5. 最終審查後加固：`integrity_status` None 防護（`or "unknown"`）＋迴歸測試。

## Global Constraints

- 工作目錄：`/home/c1147259/桌面/ISO42001/ISO42001RAG`（下稱 repo 根）。
- **禁改** `RAG/*.py`（凍結範圍）；本計畫只碰 `monitoring_addon/`、`scripts_md2html.py`、`INDEX.html`。
- **JS 契約選擇器不可變**（兩段既有 script 依賴）：`#live-content`、`#refresh-info`、`#sse-status`、`#live-dot`、`.hero`、`.alert-banner`（含 `critical|warning|calm` 變體 class）、`.alert-banner .pill`（文字格式必須維持 `CRITICAL {n}` / `WARNING {n}` / `INFO {n}`）、`.alerts-table tbody`、`.alerts-table` 的 `td` class `ts`/`sev-*`。兩段 `<script>` 內容一字不改。
- **既有測試斷言綁定的文案不可改**：「服務健康」「可用率」「近 24 小時延遲狀態格」「目前最近有資料時段：正常」「防護守則觸發」「SAFETY_CONTROLS.md」。
- 全部渲染輸出**不得含 emoji**（含 ✅⚠️❌🟢🔴⚪🖨 與 U+FE0F 變體選擇器）。幾何符號 `●`（U+25CF）、`▸`（U+25B8）、`·`、`Δ`、`→` 不是 emoji，可用。
- 無 CDN、無外部字型；字型鏈 `"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif`，等寬 `"JetBrains Mono","Consolas","Courier New",monospace`。
- 無裝飾性漸層、無陰影、無圓角卡片（radius ≤ 2px 的表格格子可容忍）。**例外**：`_render_dim_score_bars` 的 `zone_bg` 分區色帶與 `_render_status_bins` 狀態格是資料語意（分數區間／延遲狀態），保留原色與原樣式。
- 狀態色（`#16a34a`/`#2563eb`/`#d97706`/`#dc2626`/`#166534`/`#92400e`/`#991b1b`）只著色於狀態值本身（文字＋色點），不做大面積底色。色點維持 RGB（使用者確認黑白列印靠文字判讀）。
- 測試命令一律在 `monitoring_addon/` 目錄下跑：`cd monitoring_addon && python3 -m pytest tests/ -v`。
- 每個 task 結束 commit；commit message 結尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: 儀表板去 emoji — 狀態標記 helper 與常數

**Files:**
- Modify: `monitoring_addon/monitoring/dashboard_render.py`（`_SEVERITY_TONE`/`_GOAL_TONE` 常數、新增 `_status_mark`、`_faith_cell`、主模板 integrity 區塊）
- Test: `monitoring_addon/tests/test_dashboard_render.py`

**Interfaces:**
- Produces: `_status_mark(level: str, text: str) -> str` — 回傳 `<span class="mark" style="color:..."><i class="dot" style="background:..."></i>TEXT</span>`；level ∈ `ok|watch|warning|critical|none`。Task 2 的章頭對照表與報告頭 meta 會用它。
- Produces: `_MARK_TONE: dict[str, str]`（level → 色碼）。

- [ ] **Step 1: 寫失敗測試（無 emoji）**

在 `monitoring_addon/tests/test_dashboard_render.py` 末尾加入：

```python
import re

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]"
)


def test_render_no_emoji():
    html = render_dashboard(copy.deepcopy(_PAYLOAD))
    m = _EMOJI_RE.search(html)
    assert not m, f"emoji found in render output: {m.group(0)!r}"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd monitoring_addon && python3 -m pytest tests/test_dashboard_render.py::test_render_no_emoji -v`
Expected: FAIL（現有輸出含 ✅ 等 emoji）

- [ ] **Step 3: 實作**

在 `dashboard_render.py`：

(a) 取代 `_SEVERITY_TONE` 與 `_GOAL_TONE`（保持三元 tuple 形狀不變）：

```python
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
```

(b) 在 `_STATUS_BIN_TONE` 定義後新增：

```python
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
```

(c) `_faith_cell` 內 `⚠ 已過期` 改為：

```python
        bits.append(f"<span style='color:#dc2626'>已過期{suffix}，請重跑 RAGAS</span>")
```

(d) 主模板「audit 鏈完整性」卡片（現為 `{'🟢 intact' if ...}` 那行）整段改為：

```python
      <div style="font-size:22px;font-weight:900;margin-bottom:6px;">
        {_status_mark({'intact': 'ok', 'broken': 'critical'}.get(integrity_status, 'watch'), integrity_status.upper())}
      </div>
```

（外層 div 原本的 color inline style 移除，顏色由 `_status_mark` 提供。）

- [ ] **Step 4: 跑全部渲染測試確認通過**

Run: `cd monitoring_addon && python3 -m pytest tests/test_dashboard_render.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add monitoring_addon/monitoring/dashboard_render.py monitoring_addon/tests/test_dashboard_render.py
git commit -m "refactor(monitoring): 儀表板狀態標記去 emoji，新增 _status_mark helper"
```

---

### Task 2: 儀表板報告書骨架重構（CSS + 主模板 + 章頭對照表）

**Files:**
- Modify: `monitoring_addon/monitoring/dashboard_render.py`（`_render_hero`、`_render_dim_strip`、新增 `_render_chapter_head`、`_render_safety_controls` 標題層級、CSS 區塊、`render_dashboard` 的 html f-string）
- Test: `monitoring_addon/tests/test_dashboard_render.py`

**Interfaces:**
- Consumes: Task 1 的 `_status_mark(level, text)`、`_MARK_TONE`。
- Produces: 新頁面結構——`<section id="exec|ch-a|ch-b|ch-c|ch-d|appendix">` 六個錨點區塊；`<nav class="toc">` 目錄。Task 6 的目視驗證依賴這些 id。
- 不變：`render_dashboard(payload: dict) -> str` 簽名；所有 `_render_*` 子函式簽名；兩段 `<script>` 原文；`_line_chart`/`_bar_chart`/`_render_status_bins`/`_render_drift_gauge`/`_render_dim_score_bars`/`_render_health_methodology`/`_render_threshold_rationale`/`_render_alerts_banner`/`_render_alerts_table` 的函式本體（僅 CSS 承接其 class）。

- [ ] **Step 1: 寫失敗測試（報告書結構 + JS 契約）**

在 `monitoring_addon/tests/test_dashboard_render.py` 末尾加入：

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd monitoring_addon && python3 -m pytest tests/test_dashboard_render.py::test_render_report_structure -v`
Expected: FAIL（尚無 section 錨點）

- [ ] **Step 3: 實作 — 取代 `_render_hero` 與 `_render_dim_strip`，新增 `_render_chapter_head`**

```python
def _render_hero(level: str, worst_dim: str) -> str:
    _, fg, label = _DIM_TONE.get(level, _DIM_TONE["ok"])
    return (
        f'<div class="verdict hero">'
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
```

- [ ] **Step 4: 實作 — `_render_safety_controls` 標題改為附錄層級**

該函式回傳字串開頭的
`<h2>防 · 防護守則觸發（Safety Controls）</h2>` 改為
`<h3 id="appendix-4">附錄四 · 防護守則觸發（Safety Controls）</h3>`（其餘不動；「防護守則觸發」字樣保留給既有測試）。

- [ ] **Step 5: 實作 — 取代 `render_dashboard` 的 CSS 與 body 模板**

`render_dashboard` 中資料準備段（函式開頭到 `overall_level, worst_dim = _compute_overall(...)`）**保持原樣**，在其後、`html = f"""` 之前加入章頭資料：

```python
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
```

然後將 `html = f"""` 起至 `<script>` 之前的整段（`<style>` 全部與 body 前半）替換為下列模板。**兩段 `<script>` 與 `</body></html>` 結尾原文照抄，不得更動。**

```python
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
  .goal-card {{ padding:0; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; margin:8px 0 16px; }}
  th {{ text-align:left; padding:7px 10px; font-size:12px; font-weight:700;
       border-top:2px solid var(--ink); border-bottom:1px solid var(--ink); }}
  td {{ padding:6px 10px; border-bottom:1px solid var(--hairline); vertical-align:top; }}
  tr:nth-child(even) td {{ background:#fafbfd; }}
  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  .card {{ border:1px solid var(--line); padding:14px 16px; }}
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
  /* 健康分數：儀表 + 維度分數帶（zone 色帶為資料語意，保留） */
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
"""
```

注意：模板中已移除原 `<h2>跨維度 · 近 24 小時告警</h2>`、原 C 章內嵌的方法論/門檻/原始量測（移至附錄）、原 `dim-context` 斜體行（由章頭對照表取代）。`html` f-string 之後緊接原本兩段 `<script>`（含 `</body></html>`）——把原始碼中 `<script>` 起至檔尾 `return html` 的內容原封接回（f-string 需以 `""" + <原script字串>` 或直接把兩段 script 留在同一個 f-string 內，維持現行單一 f-string 寫法即可，script 內容一字不改）。

- [ ] **Step 6: 跑全部渲染測試**

Run: `cd monitoring_addon && python3 -m pytest tests/test_dashboard_render.py -v`
Expected: 全部 PASS（含既有 `test_render_has_health_cards_no_psi`、`test_render_safety_controls_block`）

- [ ] **Step 7: 跑整個 monitoring 測試套件**

Run: `cd monitoring_addon && python3 -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add monitoring_addon/monitoring/dashboard_render.py monitoring_addon/tests/test_dashboard_render.py
git commit -m "refactor(monitoring): 儀表板重構為單頁分層報告書（執行摘要/ABCD章/附錄）"
```

---

### Task 3: /audit 稽核日誌搜尋頁報告書化

**Files:**
- Modify: `monitoring_addon/monitoring/audit_search.py`（`render_audit_page` 的 `<style>` 區塊與頁首）
- Test: `monitoring_addon/tests/test_audit_search.py`

**Interfaces:**
- Consumes: 無（獨立頁面）。
- 不變：`render_audit_page(result, params, *, openwebui_available)` 簽名、表單欄位名、quick links、結果表欄位。

- [ ] **Step 1: 寫失敗測試**

在 `monitoring_addon/tests/test_audit_search.py` 加入（import 區補 `from monitoring.audit_search import render_audit_page`）：

```python
import re as _re

_EMOJI_RE = _re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿️]")


def test_render_audit_page_report_style():
    html = render_audit_page(
        {"events": [], "summary": {}, "total_seen": 0, "matched": 0, "returned": 0},
        {"window_days": 30, "limit": 200},
        openwebui_available=False,
    )
    assert "稽核日誌搜尋" in html          # 新報告書式頁首
    assert 'class="report-rule"' in html   # 與儀表板同語彙的頂部粗線
    assert not _EMOJI_RE.search(html)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd monitoring_addon && python3 -m pytest tests/test_audit_search.py::test_render_audit_page_report_style -v`
Expected: FAIL

- [ ] **Step 3: 實作**

`render_audit_page` 中 `<style>...</style>` 整段替換為：

```css
body{margin:0;background:#f6f8fc;color:#1a1f2c;font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;font-size:15px;line-height:1.7}
.page{max-width:1440px;margin:0 auto;padding:40px 48px 56px;background:#fff;min-height:100vh;border-left:1px solid #c9d1dc;border-right:1px solid #c9d1dc}
h1{font-size:24px;font-weight:900;margin:0}
.title-en{font-size:13px;font-weight:500;color:#5b6578;margin-left:10px}
.report-rule{border:0;border-top:2px solid #1a1f2c;margin:14px 0 10px}
.sub{color:#5b6578;font-size:13px;margin-bottom:18px}
form{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:10px;align-items:end;border:1px solid #c9d1dc;padding:14px}
label{font-size:12px;font-weight:700;color:#4b5568}
input,select{width:100%;padding:8px;border:1px solid #c9d1dc;background:#fff;font-family:inherit;font-size:13px}
button,.link{display:inline-block;padding:9px 14px;border:1px solid #1e3a8a;background:#1e3a8a;color:#fff;text-decoration:none;font-weight:800;cursor:pointer;font-size:13px}
.quick{margin:12px 0;display:flex;gap:8px;flex-wrap:wrap}
.quick a{font-size:12px;color:#1e3a8a;border:1px solid #c9d1dc;padding:5px 10px;text-decoration:none;font-weight:700}
.quick a:hover{border-color:#1e3a8a}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;color:#4b5568;font-size:12px}
.stats code{background:#eef2f7;padding:2px 5px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;padding:8px;font-weight:700;border-top:2px solid #1a1f2c;border-bottom:1px solid #1a1f2c}
td{border-bottom:1px solid #e5eaf1;padding:8px;vertical-align:top;line-height:1.5}
tr:nth-child(even) td{background:#fafbfd}
.ts{white-space:nowrap;font-family:"JetBrains Mono","Consolas",monospace;color:#4b5568}
.badge{display:inline-block;padding:1px 8px;font-weight:900;font-size:11px;border:1px solid currentColor}
.critical{color:#991b1b}.warning{color:#92400e}.normal{color:#166534}
.note{font-size:12px;color:#5b6578;margin:12px 0;line-height:1.6}
code{font-family:"JetBrains Mono","Consolas",monospace}
@media(max-width:980px){form{grid-template-columns:1fr 1fr}table{font-size:12px}}
@media print{body{background:#fff}.page{border:0;padding:14px;max-width:none}form,.quick{display:none}}
```

頁首兩行（`<h1>Audit Log Search</h1>` 與 `.sub`）替換為：

```html
<h1>稽核日誌搜尋<span class="title-en">Audit Log Search</span></h1>
<hr class="report-rule">
<div class="sub">查詢 RAG audit JSONL；danger 包含 security_alert、auth_failure、anomaly_flags 與 P95/單筆延遲異常。OpenWebUI DB：{"available" if openwebui_available else "not mounted"}</div>
```

其餘 body 標記（表單、quick、stats、結果表）不動；quick 區的「回 Service Status」連結文字改為「回服務狀態報告」。

- [ ] **Step 4: 跑測試**

Run: `cd monitoring_addon && python3 -m pytest tests/test_audit_search.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add monitoring_addon/monitoring/audit_search.py monitoring_addon/tests/test_audit_search.py
git commit -m "refactor(monitoring): audit 搜尋頁套用報告書視覺系統"
```

---

### Task 4: scripts_md2html.py 文件模板改版與全站重產

**Files:**
- Modify: `scripts_md2html.py`（`CSS` 與 `PAGE` 常數）
- 重產：repo 內所有由它產生的 `.html`

**Interfaces:**
- 不變：`convert()`/`collect()`/`rewrite_md_links()`、CLI 用法、輸出路徑規則。

- [ ] **Step 0: EXCLUDE_DIRS 排除內部開發文件（使用者 2026-07-08 決議）**

`scripts_md2html.py` 的 `EXCLUDE_DIRS` 加入 `"superpowers"`：

```python
EXCLUDE_DIRS = {".pytest_cache", "__pycache__", "_dev_archive", "node_modules",
                "converted_md", ".venv", "venv", "superpowers"}
```

（`docs/superpowers/` 為內部開發文件，不得混入稽核文件集；用目錄名 `superpowers` 而非 `docs`，因為 `RAG/docs/` 必須繼續轉換。）

- [ ] **Step 1: 取代 `CSS` 常數**

```python
CSS = """
:root{--ink:#1a1f2c;--muted:#5b6578;--line:#c9d1dc;--hairline:#e5eaf1;--bg:#fff;--soft:#f6f8fc;--accent:#1e3a8a;
      --mono:"JetBrains Mono","Consolas","Courier New",monospace;}
*,*::before,*::after{box-sizing:border-box;}
body{font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;
     margin:0;background:var(--soft);color:var(--ink);line-height:1.75;font-size:15px;}
.wrap{max-width:960px;margin:0 auto;padding:0 0 60px;background:var(--bg);
      border-left:1px solid var(--line);border-right:1px solid var(--line);min-height:100vh;}
.dochead{padding:22px 40px 0;}
.dochead .row{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;
              font-size:12px;color:var(--muted);}
.dochead .row code{background:none;padding:0;color:var(--muted);}
.dochead .row a{color:var(--accent);text-decoration:none;font-weight:700;}
.dochead .row a:hover{text-decoration:underline;}
.dochead hr{border:0;border-top:2px solid var(--ink);margin:10px 0 0;}
.content{padding:20px 40px 0;}
h1{font-size:26px;font-weight:900;margin:.4em 0 .5em;padding-bottom:.3em;border-bottom:1px solid var(--ink);}
h2{font-size:20px;font-weight:800;margin:1.6em 0 .6em;padding-bottom:.25em;border-bottom:1px solid var(--hairline);}
h2::before{content:"";display:inline-block;width:4px;height:.85em;background:var(--accent);
           margin-right:10px;vertical-align:-.08em;}
h3{font-size:16.5px;font-weight:800;margin:1.3em 0 .5em;}
h4{font-size:14.5px;font-weight:800;color:#374151;margin:1.1em 0 .4em;}
p{margin:.7em 0;text-wrap:pretty;}
a{color:var(--accent);}
ul,ol{margin:.6em 0;padding-left:1.7em;}
li{margin:.25em 0;}
blockquote{margin:1em 0;padding:.6em 16px;border-left:2px solid var(--ink);
           background:var(--soft);color:#374151;}
blockquote p{margin:.3em 0;}
code{font-family:var(--mono);font-size:.88em;background:#eef2f7;color:#0f172a;padding:.12em .4em;}
pre{background:var(--soft);color:var(--ink);border:1px solid var(--line);padding:14px 16px;
    overflow-x:auto;font-size:13px;line-height:1.55;}
pre code{background:none;color:inherit;padding:0;}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:13.5px;display:block;overflow-x:auto;}
th{text-align:left;padding:8px 11px;font-weight:700;white-space:nowrap;
   border-top:2px solid var(--ink);border-bottom:1px solid var(--ink);}
td{padding:7px 11px;border-bottom:1px solid var(--hairline);vertical-align:top;}
tr:nth-child(even) td{background:#fafbfd;}
hr{border:0;border-top:1px solid var(--line);margin:1.6em 0;}
img{max-width:100%;}
.docfoot{margin:36px 40px 0;padding-top:14px;border-top:2px solid var(--ink);
         font-size:11.5px;color:var(--muted);}
details{border:1px solid var(--line);margin:1em 0;padding:0 14px;}
summary{cursor:pointer;font-weight:700;padding:10px 0;color:var(--accent);}
@media print{body{background:#fff;}.wrap{border:0;max-width:none;}
             .dochead .row a{display:none;}
             pre,blockquote,table{break-inside:avoid-page;}}
"""
```

- [ ] **Step 2: 取代 `PAGE` 常數（去 🖨、docbar 改白底報告頭）**

```python
PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="dochead">
    <div class="row">
      <span>ISO 42001 RAG · <code>{relpath}</code></span>
      <span><a href="javascript:window.print()">列印 / 存 PDF</a></span>
    </div>
    <hr>
  </div>
  <div class="content">
{body}
  </div>
  <div class="docfoot">
    本檔由 <code>scripts_md2html.py</code> 自 <code>{relpath}</code> 產生 · 自包含，可離線開啟。
    原始 Markdown 為版控來源；此 HTML 供內網無 .md 解析器之環境檢視。
  </div>
</div>
</body>
</html>
"""
```

- [ ] **Step 3: 重產全站並驗證**

Run: `python3 scripts_md2html.py`
Expected: `完成：N/N 個 .md → .html`（N 與 `--list` 相同，無 ✗ 錯誤行）

Run: `grep -c 'class="dochead"' README.html AUDIT_EVIDENCE_INDEX.html RAG/README.html`
Expected: 每檔 1

Run: `grep -l '🖨' *.html RAG/*.html RAG/docs/*.html RAG/docs/governance/*.html 2>/dev/null; echo "exit=$?"`
Expected: 無檔名輸出（🖨 已消失）

- [ ] **Step 4: Commit**

```bash
git add scripts_md2html.py '*.html'
git add -A -- '*.html'
git commit -m "refactor(docs): md2html 模板改版為印刷報告書風並重產全站 HTML"
```

---

### Task 5: INDEX.html 文件總覽改版

**Files:**
- Modify: `INDEX.html`（手寫檔，非 md2html 產物——整檔重寫）

**Interfaces:**
- 不變：全部 20 個文件連結的 href 與分組歸屬（①稽核總覽 ②治理文件 ③系統與合規規格 ④變更紀錄與說明）、版本標示 v1.1.0。

- [ ] **Step 1: 整檔重寫 `INDEX.html`**

以下為完整新檔內容（連結與說明文字自現行檔案原樣保留，僅結構與樣式改為報告書風；卡片格改為分組表格，欄位＝文件／說明／ISO 對應）：

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ISO 42001 RAG — 文件總覽</title>
<style>
:root{--ink:#1a1f2c;--muted:#5b6578;--line:#c9d1dc;--hairline:#e5eaf1;--bg:#fff;--soft:#f6f8fc;--accent:#1e3a8a;
      --mono:"JetBrains Mono","Consolas","Courier New",monospace;}
*,*::before,*::after{box-sizing:border-box;}
body{font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;
     margin:0;background:var(--soft);color:var(--ink);line-height:1.7;font-size:15px;}
.wrap{max-width:1040px;margin:0 auto;background:var(--bg);min-height:100vh;
      border-left:1px solid var(--line);border-right:1px solid var(--line);padding:40px 44px 56px;}
h1{font-size:25px;font-weight:900;margin:0;}
.title-en{font-size:13px;font-weight:500;color:var(--muted);margin-left:10px;}
.report-rule{border:0;border-top:2px solid var(--ink);margin:14px 0 0;}
.meta{display:flex;gap:28px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);
      padding:10px 0;border-bottom:1px solid var(--hairline);}
.meta b{color:var(--ink);}
.cat{margin:32px 0 4px;font-size:17px;font-weight:800;padding-bottom:6px;border-bottom:1px solid var(--ink);}
.cat .no{color:var(--accent);margin-right:8px;}
.cat .hint{font-size:12px;font-weight:500;color:var(--muted);margin-left:8px;}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0 0;}
th{text-align:left;padding:7px 12px 7px 0;font-size:12px;font-weight:700;
   border-bottom:1px solid var(--ink);color:var(--muted);}
td{padding:9px 12px 9px 0;border-bottom:1px solid var(--hairline);vertical-align:top;}
td.doc a{font-weight:800;color:var(--accent);text-decoration:none;white-space:nowrap;}
td.doc a:hover{text-decoration:underline;}
td.iso{font-family:var(--mono);font-size:11.5px;color:#374151;white-space:nowrap;}
td.desc{color:#3b4252;}
.foot{margin-top:36px;padding-top:14px;border-top:2px solid var(--ink);
      font-size:11.5px;color:var(--muted);}
code{font-family:var(--mono);font-size:.9em;background:#eef2f7;padding:.1em .35em;}
@media (max-width:760px){td.doc a{white-space:normal;}}
@media print{body{background:#fff;}.wrap{border:0;max-width:none;padding:14px;}}
</style>
</head>
<body>
<div class="wrap">
  <h1>ISO 42001 RAG 法律文件查詢系統 — 文件總覽<span class="title-en">Document Index</span></h1>
  <hr class="report-rule">
  <div class="meta">
    <span>當前版本 <b>v1.1.0</b>（前一版 v1.0.0）</span>
    <span>外部稽核文件入口</span>
    <span>所有文件為自包含 HTML，無需 .md 解析器，可離線開啟、列印、郵寄</span>
  </div>

  <div class="cat"><span class="no">①</span>稽核總覽<span class="hint">先看這三份</span></div>
  <table>
    <thead><tr><th>文件</th><th>說明</th><th>ISO 對應</th></tr></thead>
    <tbody>
      <tr><td class="doc"><a href="AUDIT_EVIDENCE_INDEX.html">稽核證據索引</a></td><td class="desc">每項 ISO 要求對應到證據檔與狀態的總導覽；含待辦與預期問答</td><td class="iso">總入口</td></tr>
      <tr><td class="doc"><a href="RAG/docs/SYSTEM_ARCHITECTURE_ANALYSIS.html">系統架構分析</a></td><td class="desc">部署拓撲、AI 管線、資料生命週期、安全縱深與稽核機制；附 file:line 證據與已驗證限制</td><td class="iso">4.1 / 6.1 / A.4 / A.6.2</td></tr>
      <tr><td class="doc"><a href="PROJECT_STRUCTURE.html">專案結構導覽</a></td><td class="desc">整個部署的目錄地圖——哪些是原始碼/證據/建置產物</td><td class="iso">導覽</td></tr>
    </tbody>
  </table>

  <div class="cat"><span class="no">②</span>治理文件<span class="hint">角色、風險、倫理、人為監督</span></div>
  <table>
    <thead><tr><th>文件</th><th>說明</th><th>ISO 對應</th></tr></thead>
    <tbody>
      <tr><td class="doc"><a href="RAG/docs/governance/MODEL_CARD.html">模型卡</a></td><td class="desc">LLM / Embedding 版本、用途、限制、評估結果</td><td class="iso">A.4</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/RACI_MATRIX.html">角色職責矩陣（RACI）</a></td><td class="desc">各活動的負責/當責/諮詢/告知分工</td><td class="iso">5.3 / A.3.2</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/AI_RISK_ASSESSMENT.html">AI 風險評估</a></td><td class="desc">10 項 AI 風險 + 架構面技術風險登錄</td><td class="iso">6.1.2 / 8.2</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/AI_IMPACT_ASSESSMENT.html">AI 影響評估</a></td><td class="desc">利害關係人、權益影響、緩解對應</td><td class="iso">6.1.4 / 8.4</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/ETHICS_CHECKLIST.html">AI 倫理審查清單</a></td><td class="desc">影響/偏誤/透明/資料治理/安全/人監 六區（每季）</td><td class="iso">A.5</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/HUMAN_OVERSIGHT.html">人為監督程序</a></td><td class="desc">四查核點 + 決策權限矩陣</td><td class="iso">A.9</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/INCIDENT_RESPONSE.html">事件回應程序</a></td><td class="desc">事件分類、回應流程、SLA、通報矩陣</td><td class="iso">A.8</td></tr>
      <tr><td class="doc"><a href="RAG/docs/governance/DEPLOYMENT_HARDENING.html">部署強化指引</a></td><td class="desc">R-INFRA 風險修正方案與套用步驟</td><td class="iso">A.8 / 8</td></tr>
    </tbody>
  </table>

  <div class="cat"><span class="no">③</span>系統與合規規格</div>
  <table>
    <thead><tr><th>文件</th><th>說明</th><th>ISO 對應</th></tr></thead>
    <tbody>
      <tr><td class="doc"><a href="RAG/docs/SAFETY_CONTROLS.html">系統防護規則規格</a></td><td class="desc">9 道守則、8 種威脅偵測、守則↔程式碼對照</td><td class="iso">A.8</td></tr>
      <tr><td class="doc"><a href="RAG/docs/PROMPT_VERSIONS.html">Prompt 基線版本管理</a></td><td class="desc">單一 SYSTEM_PROMPT_BASELINE、hash 對照與升版規則</td><td class="iso">A.4</td></tr>
      <tr><td class="doc"><a href="RAG/docs/AUDIT_LOG_SCHEMA.html">稽核日誌格式規範</a></td><td class="desc">8 種事件、防竄改 SHA-256 雜湊鏈</td><td class="iso">A.5.28 / A.8.15</td></tr>
      <tr><td class="doc"><a href="RAG/docs/requirements_review_report.html">需求審查報告</a></td><td class="desc">v1.0.0 首發需求對應與 Gap 分析</td><td class="iso">需求</td></tr>
      <tr><td class="doc"><a href="RAG/docs/INTRANET_DEPLOYMENT_RUNBOOK.html">內網部署 Runbook</a></td><td class="desc">全新安裝：部署 9 步驟、Pre-flight 檢驗、緊急回退</td><td class="iso">部署</td></tr>
      <tr><td class="doc"><a href="RAG/docs/INTRANET_UPDATE_PROCEDURE.html">內網更新流程 v1.0.0→v1.1.0</a></td><td class="desc">增量更新：備份、載入 image、保留稽核日誌、連線設定、驗證、回退</td><td class="iso">更新</td></tr>
    </tbody>
  </table>

  <div class="cat"><span class="no">④</span>變更紀錄與說明</div>
  <table>
    <thead><tr><th>文件</th><th>說明</th><th>ISO 對應</th></tr></thead>
    <tbody>
      <tr><td class="doc"><a href="RAG/CHANGELOG.html">變更紀錄（CHANGELOG）</a></td><td class="desc">v1.0.0 → v1.1.0 版本歷史與升級路徑</td><td class="iso">A.6.2.5</td></tr>
      <tr><td class="doc"><a href="README.html">系統 README</a></td><td class="desc">整體架構、服務、部署流程</td><td class="iso">說明</td></tr>
      <tr><td class="doc"><a href="RAG/README.html">RAG 系統 README</a></td><td class="desc">核心功能、技術堆疊、API、scripts 手冊</td><td class="iso">說明</td></tr>
    </tbody>
  </table>

  <div class="foot">
    ISO 42001 RAG 法律文件查詢系統 · 文件總覽 · 由 <code>scripts_md2html.py</code> 產生對應 HTML<br>
    原始 Markdown 為版控來源；此批 HTML 供內網（無 .md 解析器）檢視。重新產生：<code>python3 scripts_md2html.py</code>
  </div>
</div>
</body>
</html>
```

- [ ] **Step 2: 驗證連結完整性**

Run: `grep -o 'href="[^"]*"' INDEX.html | sort > /tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad/new_links.txt && git show HEAD:INDEX.html | grep -o 'href="[^"]*"' | sort | diff - /tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad/new_links.txt`
Expected: 無差異輸出（20 個連結一個不少、一個不多）

- [ ] **Step 3: Commit**

```bash
git add INDEX.html
git commit -m "refactor(docs): INDEX 文件總覽改版為報告書式分組目錄表"
```

---

### Task 6: Playwright 目視驗證

**Files:**
- Create（scratchpad，不入版控）: `/tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad/render_samples.py`

**Interfaces:**
- Consumes: Task 2 的 section 錨點 id、Task 3/4/5 的產出檔。

- [ ] **Step 1: 產生樣本頁**

寫入 `render_samples.py`：

```python
import sys
from pathlib import Path

sys.path.insert(0, "/home/c1147259/桌面/ISO42001/ISO42001RAG/monitoring_addon")

from tests.test_dashboard_render import _PAYLOAD  # 直接重用測試 payload
import copy

from monitoring.dashboard_render import render_dashboard
from monitoring.audit_search import render_audit_page

out = Path("/tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad")
p = copy.deepcopy(_PAYLOAD)
p["daily_series"] = [
    {"date": f"07/{d:02d}", "queries": 30 + d, "rejection_rate": 0.05, "avg_latency_ms": 1200 + 40 * d}
    for d in range(1, 8)
]
p["alerts"]["recent"] = [
    {"timestamp": "2026-07-08T09:15:00+08:00", "severity": "warning",
     "source": "drift", "title": "P95 latency deviation", "message": "P95 超出基線 32%"},
]
p["alerts"]["counts_24h"] = {"info": 0, "warning": 1, "critical": 0}
(out / "dashboard_sample.html").write_text(render_dashboard(p), encoding="utf-8")
(out / "audit_sample.html").write_text(
    render_audit_page(
        {"events": [], "summary": {}, "total_seen": 3, "matched": 0, "returned": 0},
        {"window_days": 30, "limit": 200},
        openwebui_available=False,
    ),
    encoding="utf-8",
)
print("ok")
```

Run: `python3 /tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad/render_samples.py`
Expected: `ok`

- [ ] **Step 2: Playwright 逐頁截圖檢查**

用 Playwright MCP 依序 navigate 到下列 `file://` URL，各截一張全頁圖並目視檢查：

1. `file:///tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad/dashboard_sample.html` — 檢查：報告頭粗線、目錄錨點、執行摘要兩塊 verdict 並排、A/B/C 章頭對照表、24h 延遲狀態格仍為格狀、附錄四段齊全、無 emoji、無漸層底
2. `file:///tmp/claude-1026/-home-c1147259----ISO42001-ISO42001RAG/8fdfb768-9681-480a-b698-f0b64d3bc364/scratchpad/audit_sample.html` — 檢查：報告頭、表單細線框、booktabs 表格
3. `file:///home/c1147259/桌面/ISO42001/ISO42001RAG/INDEX.html` — 檢查：無漸層 hero、四組分類表、連結完整
4. `file:///home/c1147259/桌面/ISO42001/ISO42001RAG/README.html` — 檢查：dochead 白底報告頭、booktabs 表格、淺色 pre

注意：dashboard_sample 以 file:// 開啟時，自動更新 fetch 與 SSE 會連線失敗（顯示「更新失敗，重試中」「SSE 中斷」）——這是預期行為，不是 bug；console 僅允許此二類網路錯誤。

- [ ] **Step 3: 修正發現的視覺問題並重截**

任何 spacing/斷行/表格溢出問題就地修 CSS，重跑對應測試，再截圖確認。修正後：

```bash
git add -A
git commit -m "fix(frontend): 目視驗證後的視覺微調"
```

（若無問題則略過此 commit。）
