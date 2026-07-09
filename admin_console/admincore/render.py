"""維運管理台頁面渲染：單頁四區塊，沿用印刷報告書視覺 token。"""
from __future__ import annotations

from html import escape

from .envstore import SETTINGS

_CSS = """
:root { --ink:#1a1f2c; --muted:#5b6578; --line:#c9d1dc; --hairline:#e5eaf1;
        --paper:#fff; --soft:#f6f8fc; --accent:#1e3a8a;
        --mono:"JetBrains Mono","Consolas","Courier New",monospace; }
*,*::before,*::after { box-sizing:border-box; }
body { font-family:"Noto Sans TC","Microsoft JhengHei","PingFang TC",sans-serif;
       margin:0; background:var(--soft); color:var(--ink); font-size:15px; line-height:1.7; }
.report { max-width:1080px; margin:0 auto; padding:36px 44px 56px; background:var(--paper);
          border-left:1px solid var(--line); border-right:1px solid var(--line); min-height:100vh; }
h1 { font-size:23px; font-weight:900; margin:0; }
.title-en { font-size:13px; font-weight:500; color:var(--muted); margin-left:10px; }
.report-rule { border:0; border-top:2px solid var(--ink); margin:14px 0 0; }
.meta { display:flex; gap:24px; flex-wrap:wrap; font-size:12.5px; color:var(--muted);
        padding:10px 0; border-bottom:1px solid var(--hairline); }
.meta b { color:var(--ink); }
section { margin-top:32px; }
h2 { font-size:17px; font-weight:900; margin:0 0 10px; padding-bottom:6px;
     border-bottom:1px solid var(--ink); }
h2 .en { font-size:12px; font-weight:500; color:var(--muted); margin-left:8px; }
table { width:100%; border-collapse:collapse; font-size:13px; margin:8px 0 12px; }
th { text-align:left; padding:7px 10px; font-size:12px; font-weight:700;
     border-top:2px solid var(--ink); border-bottom:1px solid var(--ink); }
td { padding:6px 10px; border-bottom:1px solid var(--hairline); vertical-align:middle; }
input,select { padding:6px 8px; border:1px solid var(--line); background:#fff;
               font-family:inherit; font-size:13px; }
input.num { font-family:var(--mono); width:9em; }
button { padding:8px 14px; border:1px solid var(--accent); background:var(--accent);
         color:#fff; font-weight:800; font-size:13px; cursor:pointer; }
button.ghost { background:#fff; color:var(--accent); }
.num { font-family:var(--mono); font-variant-numeric:tabular-nums; }
.badge { display:inline-block; padding:1px 8px; font-size:11px; font-weight:800;
         border:1px solid currentColor; white-space:nowrap; }
.badge.dirty { color:#92400e; }
.badge.ok { color:#166534; }
.note { font-size:12px; color:var(--muted); }
.banner { padding:10px 14px; margin:14px 0; font-size:13px; font-weight:700;
          border:1px solid var(--line); }
.banner.err { color:#991b1b; border-color:#991b1b; }
.banner.ok { color:#166534; }
.jobbox { border:1px solid var(--line); padding:12px 16px; margin:10px 0; }
pre.tail { background:var(--soft); border:1px solid var(--hairline); padding:10px 12px;
           font-size:12px; font-family:var(--mono); max-height:260px; overflow:auto;
           white-space:pre-wrap; margin:8px 0 0; }
.oprow { display:flex; gap:10px; flex-wrap:wrap; align-items:end; margin:10px 0; }
.oprow label { font-size:12px; font-weight:700; color:var(--muted); display:block; }
.footer { margin-top:36px; padding-top:12px; border-top:2px solid var(--ink);
          font-size:11px; color:var(--muted); }
"""

_JS = """
function poll() {
  fetch('api/jobs/current').then(r => r.json()).then(j => {
    var box = document.getElementById('job-panel');
    if (!box) return;
    if (j.state === 'idle') { box.textContent = '目前沒有執行中的作業。'; return; }
    var tail = (j.tail || []).join('\\n');
    box.innerHTML = '';
    var head = document.createElement('div');
    head.textContent = '作業 ' + j.name + ' · 狀態 ' + (j.state === 'running' ? '執行中'
                     : j.state === 'done' ? '完成' : '失敗（exit ' + j.exit_code + '）')
                     + ' · 開始 ' + j.started_at;
    head.style.fontWeight = '800';
    var pre = document.createElement('pre');
    pre.className = 'tail';
    pre.textContent = tail;
    box.appendChild(head); box.appendChild(pre);
    if (j.state === 'running') setTimeout(poll, 2000);
  }).catch(function(){ setTimeout(poll, 4000); });
}
function testConnection() {
  var f = document.querySelector('form[action="api/settings"]');
  var out = document.getElementById('conn-test-result');
  out.style.color = '';
  out.textContent = '測試中…';
  var body = new URLSearchParams();
  var llm = f.querySelector('[name="LLM_API_BASE"]');
  var emb = f.querySelector('[name="EMBED_API_BASE"]');
  if (llm) body.append('llm_base', llm.value);
  if (emb) body.append('embed_base', emb.value);
  fetch('api/test-connection', {method:'POST', body:body})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d.message) { out.textContent = d.message; out.style.color = '#92400e'; return; }
      var lines = [];
      for (var k in d.results) { lines.push(k + '：' + d.results[k].detail); }
      out.textContent = lines.join('　｜　');
      out.style.color = d.ok ? '#166534' : '#991b1b';
    })
    .catch(function(){ out.textContent = '測試請求失敗'; out.style.color = '#991b1b'; });
}
function restartRag() {
  if (!confirm('確定重啟 rag-api 套用設定？服務將中斷約半分鐘。')) return;
  var st = document.getElementById('rag-restart-state');
  st.textContent = '重啟中…';
  fetch('api/restart', {method:'POST'}).then(function(){
    var tries = 0;
    (function chk(){
      fetch('api/rag-health').then(r=>r.json()).then(function(h){
        if (h.ok) { st.textContent = 'rag-api 已恢復，生效值請重新整理頁面確認。'; }
        else if (++tries < 30) setTimeout(chk, 2000);
        else st.textContent = 'rag-api 60 秒未恢復，請查 docker logs ISO42001_rag_api';
      }).catch(function(){ if (++tries < 30) setTimeout(chk, 2000); });
    })();
  });
}
function hookForms() {
  document.querySelectorAll('form[data-ajax="1"]').forEach(function(f){
    f.addEventListener('submit', function(e){
      e.preventDefault();
      var url = f.getAttribute('action');
      if ((f.method || 'post').toUpperCase() === 'GET') {
        var q = new URLSearchParams(new FormData(f)).toString();
        fetch(url + '?' + q).then(function(r){ return r.json(); }).then(showCompare);
      } else {
        fetch(url, {method:'POST', body:new FormData(f)})
          .then(function(r){ return r.json(); })
          .then(function(j){ if (j.error) alert(j.error); poll(); });
      }
    });
  });
}
function showCompare(d) {
  var box = document.getElementById('compare-result');
  if (!box) return;
  if (d.error) { box.textContent = d.error; return; }
  function block(title, arr) {
    var s = title + '（' + arr.length + '）\\n';
    arr.forEach(function(x){ s += '  ' + x.id + ' ' + x.query + '\\n'; });
    return s;
  }
  box.textContent = block('newly_failed 新增失敗', d.newly_failed)
                  + block('newly_passed 新增通過', d.newly_passed)
                  + block('still_failed 持續失敗', d.still_failed)
                  + '基線題數 ' + d.base_n + ' · 本版題數 ' + d.cur_n;
}
document.addEventListener('DOMContentLoaded', function(){ poll(); hookForms(); });
"""

# HiPKI popup 協議：自 SRC/apps/csp-governance-ui/src/api/caAuth.js 移植
# （SRC=/home/c1147259/anila-card-login-export-20260708）。以獨立字串常數承載，
# 避免與 login 頁 f-string 的大括號互相干擾。移植自 caAuth.js 的三個 primitive：
#   runPopupRoundTrip（popup 開啟 / getTbs 回 payload / postMessage 監聽 / 依 result func 收斂）
#   signWithCard（getTbsPayload 欄位對齊 cht getTbsPackage()：tbs/tbsEncoding/hashAlgorithm/
#                 withCardSN/pin/nonce/func=MakeSignature/signatureType=PKCS7；驗 ret_code/signature）
#   loginWithCard（challenge → sign → submit 串接）
# 僅改：challenge 來源改為本站 GET api/auth/card/challenge、提交改為隱藏 form 原生 POST
# api/auth/card/verify（欄位 challenge_token/signed_data），身分/員編驗證交後端（最終真相）。
_LOGIN_JS = """
/* HiPKI popup 協議：自 SRC/apps/csp-governance-ui/src/api/caAuth.js 移植
   （SRC=/home/c1147259/anila-card-login-export-20260708）。
   整合點固定三步——保留 caAuth.js 的 popup 開啟、postMessage 監聽、
   tbsPackage 組裝與 PIN 處理邏輯，僅改端點與提交方式：
   1) fetch('api/auth/card/challenge') 取 challenge_token + nonce
   2) popup http://localhost:16888/popupForm 簽 nonce（cht mock 或真 HiPKI 元件）
   3) 把 challenge_token + 回傳的 base64 簽章填入 #card-verify-form 後 submit()
   狀態訊息寫入 #card-status（開啟 popup 中/等待插卡/驗證中/失敗原因）。 */
var CARD_COMPONENT_ORIGIN = 'http://localhost:16888';
var CARD_INSTALL_TIMEOUT_MS = 3500;
var CARD_POPUP_FEATURES = 'height=200,width=200,left=100,top=20';

function _cardStatus(msg) {
  var el = document.getElementById('card-status');
  if (el) { el.textContent = msg; }
}

// 自 caAuth.js runPopupRoundTrip 移植：開一次 popup、收到 getTbs 後回 payload、
// 等對應 result func（此處為 'sign'）回來；popup 關閉視為使用者中斷；
// 元件無回應（未安裝/未啟動）以 installTimer 收斂為錯誤。
function _runPopupRoundTrip(getTbsPayload, expectedResultFunc) {
  return new Promise(function (resolve, reject) {
    var popup = window.open(CARD_COMPONENT_ORIGIN + '/popupForm',
                            'admin-card-session', CARD_POPUP_FEATURES);
    if (!popup) {
      reject(new Error('無法開啟簽章視窗（瀏覽器可能擋了 popup）'));
      return;
    }
    var settled = false, installTimer = null, pollTimer = null;
    function cleanup() {
      window.removeEventListener('message', handler);
      if (installTimer) { clearTimeout(installTimer); installTimer = null; }
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (popup && !popup.closed) { popup.close(); }
    }
    function finalize(fn, value) {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    }
    function handler(event) {
      if (event.origin !== CARD_COMPONENT_ORIGIN) return;
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (!msg || typeof msg.func !== 'string') return;
      if (msg.func === 'getTbs') {
        if (installTimer) { clearTimeout(installTimer); installTimer = null; }
        _cardStatus('讀卡元件已就緒，請於元件視窗輸入 PIN 並簽章…');
        popup.postMessage(JSON.stringify(getTbsPayload), CARD_COMPONENT_ORIGIN);
        return;
      }
      if (msg.func === expectedResultFunc) { finalize(resolve, msg); }
    }
    window.addEventListener('message', handler);
    installTimer = setTimeout(function () {
      finalize(reject, new Error('尚未安裝或未啟動中華電信本機元件（'
                                 + CARD_COMPONENT_ORIGIN + '），請確認元件運作中。'));
    }, CARD_INSTALL_TIMEOUT_MS);
    pollTimer = setInterval(function () {
      if (popup.closed) { finalize(reject, new Error('使用者中斷簽章流程')); }
    }, 400);
  });
}

// 自 caAuth.js signWithCard 移植：以 MakeSignature payload 對 tbs（=nonce）簽 PKCS#7，
// 欄位對齊 cht getTbsPackage()；驗 ret_code 與 signature。
function _signWithCard(pin, tbs) {
  return _runPopupRoundTrip({
    tbs: tbs,
    tbsEncoding: 'NONE',
    hashAlgorithm: 'SHA256',
    withCardSN: 'false',
    pin: pin,
    nonce: '',
    func: 'MakeSignature',
    signatureType: 'PKCS7'
  }, 'sign').then(function (result) {
    if (result.ret_code != null && result.ret_code !== 0) {
      throw new Error('PIN 錯誤或卡片簽章失敗（ret_code=' + result.ret_code
                      + (result.last_error ? '，last_error=' + result.last_error : '') + '）');
    }
    if (!result.signature) {
      throw new Error('元件回傳缺少 signature');
    }
    return result.signature;
  });
}

// 自 caAuth.js loginWithCard 移植：challenge → sign → submit。第 3 步改為隱藏 form
// 原生 POST，讓後端於同一次導覽建立 session（成功 303 /、失敗回登入頁帶錯誤）。
function cardLogin() {
  var btn = document.getElementById('card-login-btn');
  if (btn) { btn.disabled = true; }
  _cardStatus('取得 challenge…');
  fetch('api/auth/card/challenge')
    .then(function (r) { return r.json(); })
    .then(function (ch) {
      if (!ch || !ch.challenge_token || !ch.nonce) {
        throw new Error('challenge 取得失敗，請重試');
      }
      var pin = window.prompt('請輸入憑證卡 PIN 碼');
      if (!pin) { throw new Error('未輸入 PIN 碼，已取消'); }
      _cardStatus('開啟讀卡元件…');
      return _signWithCard(pin, ch.nonce).then(function (signature) {
        _cardStatus('驗證中…');
        var form = document.getElementById('card-verify-form');
        form.challenge_token.value = ch.challenge_token;
        form.signed_data.value = signature;
        form.submit();
      });
    })
    .catch(function (err) {
      if (btn) { btn.disabled = false; }
      _cardStatus((err && err.message) ? err.message : '插卡登入失敗');
    });
}
"""


def _job_panel(job: dict | None) -> str:
    """job 面板的伺服器端初始渲染；之後由 JS poll 更新。"""
    if not job:
        return "目前沒有執行中的作業。"
    state_txt = {"running": "執行中", "done": "完成",
                 "failed": f"失敗（exit {job.get('exit_code')}）"}.get(
                     job.get("state"), str(job.get("state")))
    tail = escape("\n".join(job.get("tail") or []))
    return (f'<div style="font-weight:800;">作業 {escape(str(job.get("name", "")))} · '
            f'狀態 {escape(state_txt)} · 開始 {escape(str(job.get("started_at", "")))}</div>'
            f'<pre class="tail">{tail}</pre>')


def _settings_rows_html(rows: list[dict]) -> str:
    out = []
    for r in rows:
        s = r["spec"]
        key, typ = s["key"], s["type"]
        envv = r["env_value"] if r["env_value"] is not None else ""
        effv = r["effective_value"] if r["effective_value"] is not None else "—"
        if typ == "enum":
            opts = "".join(
                f'<option value="{escape(o)}" {"selected" if envv == o else ""}>{escape(o)}</option>'
                for o in s["options"])
            field = f'<select name="{escape(key)}"><option value="">（未設定）</option>{opts}</select>'
        else:
            cls = "num" if typ == "int" else ""
            field = (f'<input class="{cls}" name="{escape(key)}" value="{escape(str(envv))}" '
                     f'placeholder="（未設定）">')
        badge = ('<span class="badge dirty">已寫入，待重啟</span>' if r["dirty"]
                 else '<span class="badge ok">一致</span>')
        note = "改後需 reindex（見下方索引維護）" if s.get("reindex") else ""
        out.append(
            f'<tr><td><code>{escape(key)}</code><div class="note">{escape(s["label"])}'
            f'{("　" + note) if note else ""}</div></td>'
            f'<td>{field}</td>'
            f'<td class="num">{escape(str(effv))}</td>'
            f'<td>{badge}</td></tr>'
        )
    return "".join(out)


def _reports_options(reports: list[dict]) -> str:
    return "".join(f'<option value="{escape(r["file"])}">{escape(r["file"])}</option>'
                   for r in reports)


def _reports_table(reports: list[dict]) -> str:
    if not reports:
        return '<div class="note">尚無報告。跑一次 V&V 或 RAGAS 後會出現在這裡。</div>'
    rows = "".join(
        f'<tr><td><code>{escape(r["file"])}</code></td><td>{escape(r["kind"])}</td>'
        f'<td class="num">{escape(str(r["generated_at"]))}</td>'
        f'<td class="num">{escape(str(r["hit_rate"])) if r["hit_rate"] is not None else "—"}</td>'
        f'<td class="num">{r["n"] if r["n"] is not None else "—"}</td></tr>'
        for r in reports)
    return ('<table><thead><tr><th>檔名</th><th>類型</th><th>產生時間</th>'
            '<th>Hit Rate</th><th>題數</th></tr></thead><tbody>' + rows + '</tbody></table>')


def render_login_page(password_fallback: bool = False, error: str | None = None) -> str:
    err = f'<div class="banner err">{escape(error)}</div>' if error else ""
    pw_form = ""
    if password_fallback:
        pw_form = """
  <div class="note" style="margin-top:20px;border-top:1px solid var(--hairline);padding-top:12px;">
    break-glass 帳密登入（僅讀卡環境故障時使用）</div>
  <form method="post" action="login">
    <label>帳號</label><input name="username" autocomplete="username">
    <label>密碼</label><input name="password" type="password" autocomplete="current-password">
    <button type="submit" class="ghost">帳密登入</button>
  </form>"""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="UTF-8"><title>ISO 42001 維運管理台 — 登入</title><style>{_CSS}
.login-box {{ max-width:400px; margin:12vh auto 0; border:1px solid var(--ink); padding:26px 30px; background:var(--paper); }}
.login-box label {{ font-size:12px; font-weight:700; color:var(--muted); display:block; margin-top:12px; }}
.login-box input {{ width:100%; }}
.login-box button {{ margin-top:16px; width:100%; }}
#card-status {{ font-size:12.5px; color:var(--muted); margin-top:10px; min-height:1.5em; }}
</style></head>
<body>
<div class="login-box">
  <h1 style="font-size:18px;">維運管理台<span class="title-en">Operations Console</span></h1>
  <hr class="report-rule">
  {err}
  <button id="card-login-btn" type="button" onclick="cardLogin()">插卡登入（中科院憑證卡）</button>
  <div id="card-status"></div>
  <form id="card-verify-form" method="post" action="api/auth/card/verify" style="display:none;">
    <input type="hidden" name="challenge_token"><input type="hidden" name="signed_data">
  </form>
  {pw_form}
</div>
<script>{_LOGIN_JS}</script>
</body></html>"""


def render_admin_page(ctx: dict) -> str:
    saved = '<div class="banner ok">設定已寫入 .env——按「重啟 rag-api 套用」後生效。</div>' if ctx.get("saved") else ""
    error = f'<div class="banner err">{escape(ctx["error"])}</div>' if ctx.get("error") else ""
    reports = ctx.get("reports") or []
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>ISO 42001 維運管理台</title>
<style>{_CSS}</style>
</head>
<body>
<div class="report">
  <h1>ISO 42001 維運管理台<span class="title-en">Operations Console</span></h1>
  <hr class="report-rule">
  <div class="meta">
    <span>rag-api：<b>{escape(ctx.get("rag_state", "?"))}</b></span>
    <span>monitoring：<b>{escape(ctx.get("monitoring_state", "?"))}</b></span>
    <span>SMTP：<b>{"啟用" if ctx.get("smtp_enabled") is True else "關閉" if ctx.get("smtp_enabled") is False else "未知"}</b></span>
    <span>登入：中科院憑證卡（員編白名單）＋break-glass 帳密；值域治理依 ISO 稽核表單</span>
  </div>
  {saved}{error}

  <section id="settings">
    <h2>Model 設定<span class="en">Settings — 寫入 .env，重啟 rag-api 後生效</span></h2>
    <form method="post" action="api/settings">
      <table>
        <thead><tr><th>鍵</th><th>.env 值</th><th>容器內生效值</th><th>狀態</th></tr></thead>
        <tbody>{_settings_rows_html(ctx["settings_rows"])}</tbody>
      </table>
      <button type="submit">儲存到 .env</button>
      <button type="button" class="ghost" onclick="testConnection()">測試連線</button>
      <button type="button" class="ghost" onclick="restartRag()">重啟 rag-api 套用</button>
      <span id="rag-restart-state" class="note"></span>
      <div id="conn-test-result" class="note" style="margin-top:8px;"></div>
      <div class="note">建議順序：改端點 → <strong>測試連線</strong>（測表單當前值，先確認通）→ 儲存到 .env → 重啟 rag-api 套用。</div>
    </form>
  </section>

  <section id="ops">
    <h2>評估操作<span class="en">Evaluations — docker exec 至 monitoring 容器</span></h2>
    <div class="oprow">
      <form method="post" data-ajax="1" action="api/jobs/online_vv"><button>Online V&amp;V</button></form>
      <form method="post" data-ajax="1" action="api/jobs/ragas"><button>RAGAS 評估</button></form>
    </div>
    <form method="post" data-ajax="1" action="api/jobs/regression_gate" class="oprow">
      <div><label>基線報告</label><select name="baseline">{_reports_options(reports)}</select></div>
      <div><label>本版報告</label><select name="current">{_reports_options(reports)}</select></div>
      <div><label>版本標籤</label><input name="tag" value="admin-ui"></div>
      <button>Regression Gate</button>
    </form>
    <form method="post" data-ajax="1" action="api/jobs/attribution" class="oprow">
      <div><label>V&amp;V 報告</label><select name="vv_report">{_reports_options(reports)}</select></div>
      <button>歸因分析</button>
    </form>
    <div class="jobbox"><div id="job-panel">{_job_panel(ctx.get("job"))}</div></div>
    <div class="note">作業全域互斥：同時只跑一個，避免評估互搶資源失真。</div>
  </section>

  <section id="reports">
    <h2>報告檢視<span class="en">Reports</span></h2>
    {_reports_table(reports)}
    <form method="get" data-ajax="1" action="api/reports/compare" class="oprow">
      <div><label>基線</label><select name="base">{_reports_options(reports)}</select></div>
      <div><label>本版</label><select name="cur">{_reports_options(reports)}</select></div>
      <button>Per-query flip 比對</button>
    </form>
    <pre id="compare-result" class="tail"></pre>
  </section>

  <section id="alerts-ops">
    <h2>索引與告警<span class="en">Index &amp; Alerts</span></h2>
    <div class="oprow">
      <form method="post" data-ajax="1" action="api/jobs/reindex_full"><button>全量 Reindex</button></form>
    </div>
    <form method="post" data-ajax="1" action="api/jobs/version_snapshot" class="oprow">
      <div><label>變更說明</label><input name="message"></div>
      <div><label>操作者</label><input name="operator"></div>
      <div><label>版本號</label><input name="version" placeholder="v1.1.1"></div>
      <button>版本快照</button>
    </form>
    <form method="post" data-ajax="1" action="api/alert-test" class="oprow">
      <div><label>等級</label><select name="severity">
        <option value="info">info</option><option value="warning">warning</option>
        <option value="critical">critical</option></select></div>
      <button>發送測試告警</button>
    </form>
    <div class="note">測試告警會走完整管線（alerts.jsonl → SSE → SMTP 若有設）；到儀表板告警區確認收到。</div>
  </section>

  <div class="footer">操作留痕：admin_console/data/changes.jsonl · 作業紀錄：jobs.jsonl · 設定備份：env-backups/
    <form method="post" action="logout" style="display:inline;margin-left:14px;"><button class="ghost" style="padding:2px 10px;font-size:11px;">登出</button></form></div>
</div>
<script>{_JS}</script>
</body>
</html>"""
