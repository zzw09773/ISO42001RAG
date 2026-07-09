# Input Sanitizer 抗規避強化（canonicalization + 全訊息涵蓋）— 設計文件

- 日期：2026-07-09（v2，納入第二輪審查）
- 狀態：待使用者複審
- 範圍：`RAG/rag_system/core/input_sanitizer.py`、`RAG/api.py`、`RAG/rag_system/agent/nodes.py`、`RAG/rag_system/agent/react_workflow.py`（wrapper_mode 貫通 + 共用 audit helper）、測試、`SAFETY_CONTROLS.md`
- 前置：RAG/ 已解凍；系統版號維持 **v1.1.0**；此為 SAFETY_CONTROLS 守則③（input sanitizer）安全控制變更（A.8）

## 1. 背景與問題

現行 `sanitize()` 對**原始字串**逐條跑 regex，無正規化。實測確認變形全部繞過（直白形式正常擋）：

| 變形 | 範例 | 現況 |
|---|---|---|
| URL 編碼 | `ignore%20previous%20instructions` | 漏 |
| 拆字 | `i g n o r e previous instructions` | 漏 |
| 整數/十六進位/IPv6/短式 IP | `http://2130706433/`、`0x7f000001`、`[::1]`、`127.1` | 漏 |
| SQL 註解拆解 | `UN/**/ION`、`UN--\nION SELECT` | 漏 |
| 全形 | `ＳＹＳＴＥＭ： override rules` | 漏 |
| 零寬字元 | `act​as a hacker` | 漏 |

結構性缺口：
- 只檢查當前 `question`；`api.py` 轉入 client 的 system 與**歷史**，且 `ConversationStore` 的 **DB 歷史也 prepend** 進 graph（`api.py:322`）——皆未 sanitize。
- 「sql密碼」等憑證探測變體因 pattern 過窄漏接。

## 2. 核心決策（第一輪確認）

| 決策 | 結論 |
|---|---|
| 正規化定位 | **只用於偵測**；下游送清洗後文字，不改寫合法查詢語意 |
| 檢查範圍 | **所有進入 graph 的非「系統產生」訊息**（見 §3-B3 精確定義） |
| `### Task:` 背景任務 | **不偵測包裝**（跳過 injection/role-switch/system-probe），仍強制長度＋SSRF/SQL/LDAP/CSRF |

## 3. 邊界契約（兩輪審查逐條落地）

### B1. `canonicalize()` 回傳結構、非單一字串
回傳 `CanonicalViews` dataclass：

```python
@dataclass(frozen=True)
class CanonicalViews:
    normalized: str        # NFKC + 去零寬 + URL-decode（≤2 次）；一般 regex 用
    collapsed: str         # normalized 再去所有非英數（\W_）、小寫；拆字關鍵詞比對用
    sql_view: str          # normalized 再移除 SQL 註解（/*..*/、-- 、#）；SQL pattern 用
    hosts: list[HostInfo]  # 從 normalized 抽 URL、經 urlparse+ipaddress 解析的 host 分類
```

`HostInfo = {"raw": str, "kind": "ip"|"name", "category": "loopback"|"private"|"link_local"|"metadata"|"public"|"unparseable"}`。
各 pattern 類別跑對應視圖：injection/role-switch/CSRF/system-probe → `normalized`；injection 關鍵詞白名單 → `collapsed`；SQL → `sql_view`；SSRF → `hosts`（結構化）＋既有具名 pattern（docker/k8s、非 http scheme）跑 `normalized`。

### B2. wrapper 豁免：不可偽造的信任條件 + 貫通 graph 第二道

**（a）信任邊界（回應 #3 — source_app 不可信）**
`source_app==openwebui` 由 header/referer/user-agent 推斷（`api.py:156`），可被直連 API client 偽造，**不得**作為豁免依據。改用**不可偽造的 TCP peer**：
- 沿用既有 `TRUSTED_PROXIES` 機制（`auth.py`，只信任清單內的 immediate TCP peer），新增 `WRAPPER_TRUSTED_PEERS`（env，預設空＝無人可豁免）；wrapper 豁免要求 `request.client.host ∈ WRAPPER_TRUSTED_PEERS`。內網部署將 OpenWebUI 容器/反代 IP 填入。
- **三條件全滿足才算 wrapper**：① peer ∈ WRAPPER_TRUSTED_PEERS；② 訊息比對 `_OPENWEBUI_TASK_SIGNATURES`（`### Task:` 起始 **且** 含已知任務 body 句，如 `Generate a concise, 3-5 word title`、`Suggest 3-5 relevant follow-up`、`Generate 1-3 broad tags`）；③ role ∈ {user, system}。
- 任一不符 → 當一般訊息全檢（含冒充 `### Task:` 的外部攻擊者）。

**（b）貫通 graph 第二道（回應 #1）**
現行 `classify_node`（`nodes.py:136`）先 `sanitize(question)`、後（`nodes.py:154`）才判 `### Task:` passthrough；若 pre-graph 豁免但 graph 內第二道用無 wrapper 的 sanitize，wrapper 仍被打掉。
修正：`wrapper_mode` 存入 graph state；`classify_node` 改 `sanitize(question, is_wrapper=state["wrapper_mode"])`；ReAct 的 `react_workflow.py:260/425` 同步。`sanitize()` 本身**不**自我判斷 `### Task:`（`is_wrapper` 只由外部傳入，防偽造）。

### B3. 掃描範圍精確定義（回應 #2/#4/#7）
**掃描對象＝所有「將進入 graph 且非本系統產生」的訊息**：
- `request.messages` 的 **user、system、assistant 全掃**（client 提供的 assistant 也會進 prompt history，見 `nodes.py:481`；**採「掃描 client assistant」而非丟棄**，避免改動既有多輪對話行為）。
- `ConversationStore` 的 **DB 歷史**：stored **user** 訊息掃描（防守，且涵蓋強化前寫入者）。stored **assistant** 訊息**豁免**——由本系統產生且已過 `output_filter`。
- **內部 system prompt 不掃**：`AGENT_SYSTEM_PROMPT` 在 graph 內注入——classic（`nodes.py:478`）與 ReAct（`react_workflow.py:227`）皆是，**不在 `request.messages` 亦不在 DB 歷史**，故 API pre-graph sanitizer 天然不觸及，設計明訂不得掃描任何內部提示常數。
- audit 每筆 security_alert 記 `message_index` ＋ `message_role` ＋ **`message_source`（`request` | `stored_history`）**，序列來源清楚。

### B4. SQL 註解移除：一律移除、含 line comment、pattern 容忍鄰接（回應 #6）
- `sql_view` **一律移除** `/*...*/`（non-greedy `/\*.*?\*/` + `DOTALL`，線性無回溯風險；**不設「超過 N 字不移除」的跳過**，改由既有 `MAX_INPUT_LENGTH` 為總長護欄）與 `--` 到行尾、`#` 到行尾。
- 移除採**空字串**替換，使 `UN/**/ION` → `UNION`；SQL 關鍵詞 pattern 改用 `\s*`（零或多空白）鄰接，使 `UNION/**/SELECT`→`UNIONSELECT` 亦命中。
- 額外訊號：單一 `/*...*/` 跨度異常長（如 >200 字）本身標記為可疑（不阻止移除，作為輔助 flag）。

### B5. URL decode 硬上限（回應 #5 之效能面）
percent-decode **最多 2 次**（或提前收斂即止）；輸入長度於 canonicalize 前先檢（`MAX_INPUT_LENGTH`），避免膨脹型 payload 消耗 CPU。

### B6. IP 正規化用 parser（回應 #6 之 IP 面）
`urlparse` 抽 host → `ipaddress` 嘗試解析：IPv6 bracket `[::1]`、十進位整數 `2130706433`、十六進位 `0x7f000001`、短式 `127.1`（補零）、一般點分。分類 loopback/private/link_local/metadata（`169.254.169.254`）→ 危險。**公開 host 不擋**（合法查詢引用外部網址不受影響）。非 IP 具名 host 交既有具名 SSRF pattern。

### B7. raw / cleaned 文字流向寫死（回應 #5）
- `clean_text_for_downstream(text) -> str`：**只**移除隱形/零寬字元；不做 NFKC、URL-decode、SQL 註解移除。
- **流向明訂**：
  - `raw_content`（原始輸入）→ **audit / security_alert**（保留攻擊原貌，供調查）。
  - `clean_content`（clean_text_for_downstream 後）→ **graph / LLM**、以及 `ConversationStore` 儲存。
  - 即 `api.py` 現行同時用 `last_user_content` 於三處（`api.py:312/515`）拆為 raw 與 clean 兩版：audit 用 raw、graph/store 用 clean。

### B8. API 層攔截走同一 audit/回應路徑（回應原 #8）
- `log_security_alert` 增可選欄位 `message_index`、`message_role`、`message_source`、`wrapper_mode`。
- 新增 `security_block_response(audit, *, threat_type, reason, raw_content, ..., stream)` helper（api.py）：呼叫 `audit.log_security_alert(...)` 並回傳與 graph 一致的使用者回應（重用 `SECURITY_MSG`；串流回等價 chunk 序列、非串流回 `ChatCompletionResponse`）。pre-graph 攔截**不自行 raise**。
- graph 內 `security_block_node` 既有 audit 呼叫保留，新欄位帶預設值（`message_source="request", message_index=None, wrapper_mode=False`），schema 不分裂。

## 4. 架構與資料流

```
POST /v1/chat/completions
  │  request.messages（client）；conv_store.get_history()（DB 歷史）
  ▼
[組出「將進入 graph 的訊息序列」] = DB stored_history(prepend) + request.messages
  ▼
[API 層 pre-graph 掃描]  ← 新增
  peer = request.client.host
  for (source, idx, msg) in 上述序列:
     if msg.role == "assistant" and source == "stored_history": continue   # 本系統產生、已過 output_filter
     is_wrapper = _is_openwebui_wrapper(msg, peer)          # B2：peer∈WRAPPER_TRUSTED_PEERS ∧ 簽章 ∧ role
     res = sanitize(clean_or_raw=msg.content, is_wrapper=is_wrapper)
     if res.blocked:
         return security_block_response(audit, threat_type=res.threat_type,
             reason=res.reason, raw_content=msg.content,
             message_index=idx, message_role=msg.role, message_source=source,
             wrapper_mode=is_wrapper, stream=request.stream)     # B8 共用路徑
  ▼  全部通過
  raw_last_user  = 最後一則 user 原文          → audit
  clean_messages = clean_text_for_downstream 套用各訊息 → 建 langchain_messages（graph/LLM）
  clean_last_user→ ConversationStore 儲存
  ▼
[graph]  classify_node: sanitize(question, is_wrapper=state.wrapper_mode)   # 第二道，wrapper 貫通
         security_block_node 維持（audit 帶新欄位預設）
```

`sanitize(text, is_wrapper=False)`：① 長度（原始 text）② `views=canonicalize(text)` ③ 依 `is_wrapper` 跑對應視圖的 pattern 類別 ④ 回 `SanitizeResult`（欄位不變）。

## 5. 誤判控制
- detection-only 不改寫下游語意；audit 存 raw。
- IP 只擋內網/危險 host、不擋公開 URL。
- collapsed 只比對 injection 關鍵詞**有限白名單**，不對任意子字串觸發。
- system-probe 補憑證變體維持「列舉具體詞」（`sql\s*密碼|sql\s*帳號|db\s*密碼|sql\s*password`），不泛化到單一「密碼」。
- 誤判防護測試：合法法律查詢含 `%`、公開 URL、`系統`、`密碼`（如「洩漏公務密碼的罰則」）不得被擋。

## 6. 測試計畫
1. `test_prompt_security.py` 擴充：§1 全變形轉「應擋」；誤判防護（合法不擋）。
2. 新 `test_canonicalize.py`（回應 #B9 第一類）：各視圖輸出斷言（`ＳＹＳＴＥＭ`→normalized 含 `system`；`i g n o r e`→collapsed 含 `ignore`；`2130706433`→hosts loopback；`UN/**/ION`→sql_view `UNION`）。
3. 新 `test_sanitize_coverage.py`：wrapper 模式豁免（peer 可信 + 真簽章 → 豁免 injection 但 SSRF 仍擋；peer 不可信或冒充簽章 → 全檢）；request/DB 多序列逐則檢查含 `message_source`。
4. 新 `test_api_security_e2e.py`（回應 #B9 第二類）：mock `run_query`/`astream_query`，惡意 system／前輪 user／DB 歷史／`### Task:` 內嵌 SSRF，斷言 stream 與 non-stream 皆回 block、**LLM 未被呼叫**、audit 欄位含 index/role/source/wrapper。
5. **新增：graph 層 wrapper 貫通測試（回應 #8）**：以真實 `classify_node`／ReAct 路徑（不 mock sanitizer）驗證 `is_wrapper=True` 能一路到 `passthrough`、且 wrapper 內 SSRF/SQL 仍被擋——確保第二道 sanitizer 不會打掉合法 wrapper。

## 7. 紀律（動 RAG/ 安全控制）
- 更新 `RAG/docs/SAFETY_CONTROLS.md`：守則③新增 canonicalization 層、掃描範圍（含 DB 歷史、豁免內部 prompt 與系統產生 assistant）、wrapper 信任邊界。
- online V&V + regression gate：證明正常法律查詢 hit_rate 不退步（對照變更前 baseline）。
- `version_tracker` 快照 + `CHANGELOG`（v1.1.0 維持）。
- 重建 rag-api，實機重跑 §1 全變形確認全擋、既有 79 案不退。

## 8. 範圍外
- 網路層 WAF / rate-limit（另議）。
- 丟棄 client assistant history 的更強做法（本版採「掃描」而非丟棄，減少行為變動；如需可另立變更）。
- OpenWebUI 背景任務是否完全不進安全管線（本版採 wrapper 豁免，不改 OpenWebUI）。
- LLM-based 語意偵測；assistant 模型輸出審查（已有獨立 `output_filter`）。
