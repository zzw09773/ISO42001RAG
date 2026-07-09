# Changelog

ISO42001 RAG 外部稽核準備變更紀錄。

本文件只保留外部稽核準備所需的主系統變更摘要。歷史開發報告、舊部署包紀錄與舊測試資料不列入本基線文件集。

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
