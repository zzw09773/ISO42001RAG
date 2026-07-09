# Changelog

ISO42001 RAG 外部稽核準備變更紀錄。

本文件只保留外部稽核準備所需的主系統變更摘要。歷史開發報告、舊部署包紀錄與舊測試資料不列入本基線文件集。

## 2026-07-09 — input sanitizer 抗規避強化（v1.1.0 維持）

**變更者**：龔修潁

- **Canonicalization 偵測層**（`RAG/rag_system/core/canonicalize.py`）：sanitizer 改對正規化視圖比對，不改寫送 LLM 的文字。含 NFKC（全形→半形）、去零寬/隱形字元、有界 URL-decode（≤2 次）、SQL 註解移除（`/* */` 整段、`--`/`#` 標記換空白）、IP parser（整數/十六進位/短式/IPv6 皆解析分類 loopback/private/link_local/metadata）。破解全形偽裝、零寬拆字、URL 編碼、`UN/**/ION`、非點分內網位址等規避手法。
- **掃描範圍擴及 DB 歷史**（`RAG/api.py` pre-graph）：graph/LLM 呼叫前逐則掃描所有進 graph 的「非系統產生」訊息，含 DB 取回的歷史 user 訊息，避免注入語句藏在早前對話輪；系統內部 prompt 與系統產生的 assistant（已過 Output Filter）豁免。
- **wrapper 不可偽造豁免**：OpenWebUI 背景任務（`### Task:`）豁免採「`WRAPPER_TRUSTED_PEERS` peer IP ∧ 任務簽章 ∧ role∈{user,system}」三條件 AND；信任邊界為 TCP peer IP（非可偽造的 header/source_app），env 預設空＝無人豁免，不硬編碼 IP。豁免僅放行 injection/system_probe/role_switch，長度/SSRF/SQL/LDAP/CSRF 仍強制。
- **raw/clean 分流**：raw 原始字串只進 audit（雜湊鏈稽核看到攻擊者真正送的內容）；clean（僅去隱形字元）進 graph/LLM 與入對話庫。正常查詢可見語意不變。
- **不涉及 prompt 變更**：`SYSTEM_PROMPT_BASELINE` 未動，`prompt_version_hash` 不變——本次為偵測層/流程強化，非模型行為變更。
- **配套文件**：`RAG/docs/SAFETY_CONTROLS.md` 守則③補述上述七點；`docker-compose.yaml` rag-api 加 `WRAPPER_TRUSTED_PEERS`（預設空）。
- **驗證**：實機重跑規避變形全數擋下、合法查詢無誤擋；online V&V 之 gating 業務目標 Hit Rate ≥ 0.90 仍達標（0.9355）。與變更前基線 0.9677 的一題之差經逐題 flip 分析證為檢索層非確定性噪音（兩題 eval_m07/eval_cr04 均為純中文合法查詢，`clean_text_for_downstream` 為 no-op、sanitize 未擋，graph 輸入與變更前逐位元組相同），與 sanitizer 清洗無關。

## 2026-07-09 — 回覆使用聲明改為程式保證（v1.1.0 維持）

**變更者**：龔修潁

- `RAG/api.py`：每個模型回覆末尾由程式附加固定使用聲明「本回答由 AI 依知識庫收錄之法規文件生成，僅供參考，不構成法律意見；重要決策請諮詢專業法律人員。」（ISO 42001 A.9 透明性）。串流與非串流兩條出口皆保證；對話庫與稽核日誌儲存之文字＝使用者實際收到的文字。錯誤回覆與空回覆不附加。
- **不涉及 prompt 變更**：`SYSTEM_PROMPT_BASELINE` 未動，`prompt_version_hash` 不變——聲明為系統不變量，由程式碼保證而非依賴模型遵循格式（消除聲明出現與否的機率性）。
- 評估端配套：`monitoring_addon/scripts/run_ragas_evaluation.py` 於 faithfulness 評分前剝除該固定聲明（聲明本不在檢索條文中，不應計入答案接地評分）；檢索指標（hit_rate 等）不受影響。
- 驗證：串流/非串流實測含聲明、稽核紀錄一致；online V&V + regression gate 對照變更前基線（baseline_vv_pre_disclaimer.json）確認無退步。

## 2026-07-07 — 外部稽核準備乾淨基線

**整理者**：龔修潁（RAG 相關後端）、張丘（強密碼、憑證、OpenWebUI）

### 文件整理

- 更新 `README.md`、`PROJECT_STRUCTURE.md`、`AUDIT_EVIDENCE_INDEX.md` 與 `RAG/docs/` 治理文件，敘述統一為外部稽核準備基線。
- 系統服務範圍整理為 `rag-api`、`embed-proxy`、`jupyter`、`openwebui`、`keycloak`、`code-server`、`nginx` 與 `db/pgvector` 依賴。
- 權責敘述改為龔修潁負責 RAG 相關後端與稽核日誌證據，張丘負責強密碼、憑證、OpenWebUI/Keycloak 入口。
- 刪除舊輔助文件與歷史證據索引，稽核證據改以稽核日誌、Prompt 版本、版本快照、V&V 與安全測試為主。

### 乾淨基線

- 清理歷史稽核日誌、舊版本 snapshot、舊部署包、Docker tar、舊憑證與本機機密設定。
- 保留 `.env.example`、`nginx/ssl/.gitkeep`、`RAG/data/versions/.gitkeep` 等必要骨架，正式內網啟動後由 runtime 重新產生資料。

### 內網部署

- 內網 DNS 使用 `aimla.ai.example.com`。
- 內網 80/443 已由其他服務使用，本專案 nginx 對宿主使用 `8088/8443`。
- 正式憑證未套用前，憑證可先由 `nginx/generate_certs.sh` 產生自簽版本。

## v1.1.0 — 主系統基線

- RAG API 提供 OpenAI-compatible chat completions、文件上傳、重建索引、模型清單與 healthcheck。
- Embedding 經 `embed-proxy` 轉接 Triton gRPC，LLM 經 `.env` 指向內網 vLLM。
- OpenWebUI 0.7.2 透過 RAG API 使用 `rag-agent`。
- Keycloak 26.5.6 提供 OpenWebUI OIDC 與強密碼註冊政策。
- code-server 掛載整個 ISO42001 專案，供內網維運與檢視。
- 稽核日誌採 JSONL 每日滾動與 SHA-256 雜湊鏈，Prompt 行為以 `prompt_version_hash` 追溯。

## [v1.1.0] - 2026-07-09 17:13
**操作者**：龔修潁  
**說明**：input sanitizer 抗規避強化：canonicalization + 全訊息涵蓋 + wrapper 信任邊界  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 新增檔案
- `rag_system/core/canonicalize.py`
- `tests/unit/test_api_security_e2e.py`
- `tests/unit/test_canonicalize.py`
- `tests/unit/test_sanitize_coverage.py`

### 修改檔案
- `CHANGELOG.html`
- `CHANGELOG.md`
- `api.py`
- `docs/SAFETY_CONTROLS.md`
- `rag_system/agent/graph.py`
- `rag_system/agent/nodes.py`
- `rag_system/agent/react_workflow.py`
- `rag_system/agent/state.py`
- `rag_system/core/audit_logger.py`
- `rag_system/core/input_sanitizer.py`
- `tests/evaluation/test_prompt_security.py`

---
