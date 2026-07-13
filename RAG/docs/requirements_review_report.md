# 中文法律 RAG 系統 — 需求規格審查報告

| 項目 | 內容 |
|---|---|
| **系統名稱** | 中文法律 RAG 系統（Chinese Law RAG System） |
| **目標版本** | v1.0.0（首發版本，需求單位與開發團隊需求討論階段） |
| **審查階段** | 開發前需求審查（Pre-Development Requirements Review） |
| **審查日期** | 2026-05-26 |
| **適用標準** | IEEE 830-1998 SRS、ISO/IEC 42001:2023 Annex A |
| **審查結論** | **有條件通過（Approved with Conditions）** |

---

## 1. 執行摘要

本報告針對「中文法律 RAG 系統 v1.0.0」需求規格進行開發前審查。系統目標為以檢索增強生成（RAG）技術，提供國軍法律承辦人員在**完全離線內網環境**下檢索陸海空軍懲罰法等中文法規條文的能力，並符合 **ISO/IEC 42001:2023** 之 AI 管理系統控制要求（A.5 / A.6 / A.8 / A.9）。

### 1.1 審查範圍

| 範圍 | 涵蓋 |
|---|---|
| 功能需求 | RAG 檢索流程、API 介面、文件攝取、代理工作流、部署維運 |
| 非功能需求 | 安全性、稽核維運、偏誤公平性、V&V、效能、可靠性 |
| 合規對照 | ISO 42001 Annex A 控制項追溯矩陣 |
| 不在範圍 | 上游 LLM / Embedding 模型本身的訓練、底層 Triton 推論伺服器運維 |

### 1.2 審查結論摘要

- 功能需求共 **31 項**，全數可驗證；無互斥或矛盾。
- 非功能需求共 **24 項**，其中 22 項可驗證，2 項屬「過程性要求」需以稽核流程佐證。
- 識別出 **5 項條件式改善建議**（Conditional Recommendations），均為開發起點即應落實的基線控制，**不構成阻擋（non-blocking gate）**。
- 系統範圍與業務目標一致，技術可行性高，風險可控。

### 1.3 整體評定

> 詳見 §11.1。

---

## 2. 系統概述與目標

### 2.1 業務背景

法律承辦人員在處理懲罰、申訴、覆審等案件時，需要在**短時間內精確檢索**特定條文，並能交叉比對相關規範。傳統做法（紙本法典、PDF Ctrl+F、政府公開查詢站）有以下痛點：

1. **無法在封閉內網環境使用**雲端 LLM 法律助手。
2. PDF 全文搜尋僅匹配字串，**無法處理同義詞或語意推理**（例：「停役」與「不得繼續服役」）。
3. 法規修訂頻繁，**版本控管與來源可追溯性**多依賴人工。
4. 對話式問答（追問、補充）在純文件檢索工具中**無法保留上下文**。

### 2.2 系統目的

建置一套**離線可部署、可審計、可驗證**的中文法律 RAG 系統，具備：

- 精確的條文檢索（條號、語意、混合）；
- OpenAI 相容 API（無痛接入 Open WebUI 等前端）；
- 完整的安全控制與稽核日誌；
- 符合 ISO 42001 的 AI 治理證據鏈。

### 2.3 利害關係人

| 角色 | 關切點 |
|---|---|
| 法律承辦人員（主要使用者） | 查得到、查得快、查得準；可追問 |
| 單位資安／資訊室 | 內網不外洩、存取受控、行為可稽核 |
| 法務主管／稽核單位 | 回答可追溯來源；偏誤可量化；版本可比對 |
| ISO 42001 稽核員 | 控制項證據齊備；變更可追溯；風險已評估 |
| 系統運維人員 | 容器化部署、可離線出貨、可回滾 |

### 2.4 系統邊界與範圍

```
┌──────────────────────────────────────────────────────────────┐
│                     系統範圍（in-scope）                       │
│                                                                │
│  RAG API (FastAPI)  ──  Agent (LangGraph)  ──  pgvector       │
│        │                       │                    │          │
│        └─ 認證/限流/消毒  ──── 檢索/生成/驗證 ─── 文件索引     │
│                                                                │
│  Embed Proxy（OpenAI 格式 → Triton gRPC 轉換）                 │
│  Audit Logger / Anomaly Detector / Bias Evaluator              │
└──────────────────────────────────────────────────────────────┘
        ↑                                          ↓
   ┌─推論後端相依（out-of-scope）────────────────────────┐
   │  Triton Inference Server（內網 GPU 主機，推論後端維運團隊）  │
   │  LLM Backbone（openai/gpt-oss-20b）             │
   │  Embedding Model（nvidia/nv-embed-v2）          │
   └─────────────────────────────────────────────────┘
```

---

## 3. 系統架構總覽

### 3.1 高階架構

```
[ 使用者 / Open WebUI ]
            │ HTTPS, Bearer Token
            ▼
┌─────────────────────────────────────────────────────────┐
│ RAG API (FastAPI, port 8000)                            │
│  ├── Auth (Bearer / Intranet IP / Fail-Closed)          │
│  ├── Rate Limiter (60 rpm / key, sliding window)        │
│  ├── CORS Middleware                                    │
│  └── /v1/chat/completions, /v1/upload, /v1/documents... │
└─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ LangGraph Agent Workflow                                │
│   START → classify → [retrieve|reject|security_block|   │
│                       passthrough] → generate → verify  │
│                                          │              │
│                            (needs_retry) ◀┘             │
└─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────┐
│ Retrieval Service                                       │
│   Stage 0: 條文號碼快速路徑（Article Fast-Path, BM25）  │
│   Stage 1: 混合檢索（BM25 + Vector，Round-Robin）       │
│   Stage 2: LLM Reranker（Top-3）                        │
│   Stage 3: Parent Document 還原                         │
└─────────────────────────────────────────────────────────┘
            │                       │
            ▼                       ▼
   ┌────────────────┐      ┌─────────────────┐
   │ pgvector (PG)  │      │ embed-proxy     │
   │ Vector / BM25  │      │ OpenAI ↔ Triton │
   └────────────────┘      └─────────────────┘
                                    │ gRPC
                                    ▼
                          ┌─────────────────┐
                          │ Triton Server   │
                          │  nv-embed-v2    │
                          │  gpt-oss-20b    │
                          └─────────────────┘
```

### 3.2 模組分層

| 層 | 模組 | 職責 |
|---|---|---|
| 介面層 | `api.py` | OpenAI 相容 HTTP、認證、限流、CORS |
| 工作流層 | `rag_system/agent/` | LangGraph 節點、Agent 狀態、對話記憶 |
| 服務層 | `rag_system/services/` | 文件攝取、混合檢索、格式轉換、對話儲存 |
| 核心層 | `rag_system/core/` | 設定、工廠、Prompt、安全控制、稽核、評估 |
| 維運層 | `scripts/` | Reindex、稽核摘要、V&V 評估、版本追蹤 |

### 3.3 部署拓樸

- 容器化：Docker Compose 統一管理。
- 離線：`save_images.sh` 在可建置機器將所有映像打包為 `.tar`，再搬運至內網。`deploy.sh` 以 `--no-build --pull never` 啟動，乾淨 checkout 若沒有完整 image tar 或預先載入的 images 會拒絕部署。
- 推論後端依賴：僅 Triton GPU 主機，透過內網 IP 連線。
- 持久化：pgvector volume、`data/audit_logs/`、`data/versions/`、`data/converted_md/`。

---

## 4. 功能需求（Functional Requirements）

> 所有需求採「The system shall ...」句型；每項給 FR-XX-NN 編號。
> 「可驗證性」欄位採三級：✅ 完全可驗證（自動測試）、◐ 部分可驗證（需人工抽樣）、◌ 流程性（需文件佐證）。

### 4.1 RAG 核心檢索與生成（FR-R）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| FR-R-01 | 系統**應**支援以阿拉伯數字與中文數字辨識條文號碼（例：「第46條」「第四十六條」），並啟動快速路徑（Article Fast-Path）以 BM25 精確比對直接回傳。 | ✅ | 單元測試：對 30 筆條號變體進行偵測。 |
| FR-R-02 | 系統**應**提供混合檢索：BM25 關鍵字 + Vector 語意搜尋，並以 Round-Robin 合併結果。 | ✅ | 單元測試：模擬只 BM25 命中 / 只 Vector 命中 / 兩者命中三種情境。 |
| FR-R-03 | 系統**應**對候選 chunks 執行 LLM Reranker，取 Top-3 進入後續流程。 | ✅ | 整合測試：黃金資料集 Top-3 命中率。 |
| FR-R-04 | 系統**應**採用 Parent-Child 階層式索引；以小 chunk 做向量搜尋，命中後**取回完整父文件段落**作為上下文。 | ✅ | 單元測試：驗證父文件 ID 一致性。 |
| FR-R-05 | 系統**應**以 LangGraph 實作 Agent 工作流，節點包含：`classify → retrieve → generate → verify` 主流程，與 `reject / security_block / passthrough` 分支。 | ✅ | 圖結構單元測試 + 路由測試。 |
| FR-R-06 | 系統**應**在回答產生後執行 `verify_node` 檢查引用格式與 citation provenance；驗證失敗**應**重試（上限 `MAX_RETRIES=2`）。若無據條號在重試耗盡後仍存在，系統**應**以 fail-safe 訊息取代原回答。 | ✅ | 整合測試：強制注入錯誤條號，驗證重試與額度耗盡後的安全取代。 |
| FR-R-07 | 系統**應**支援多輪對話：以 `x-session-id` HTTP header 識別會話，並透過摘要壓縮控制上下文長度（目標 ≤ 3000 tokens）。 | ✅ | 單元測試：模擬 10 輪對話的記憶壓縮輸出 token 數。 |
| FR-R-08 | 系統**應**對非法律領域之查詢由 `classify_node` 判定為 out-of-scope 並以 `reject_node` 回應標準拒絕訊息。 | ✅ | 黃金資料集 `category=out_of_scope` 案例。 |

### 4.2 API 與介面層（FR-A）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| FR-A-01 | 系統**應**提供 OpenAI 相容 `POST /v1/chat/completions` 端點，支援 streaming 與 non-streaming 兩種模式。 | ✅ | 對齊 OpenAI SDK 整合測試。 |
| FR-A-02 | 系統**應**提供 `GET /v1/models` 以供前端探測可用模型清單。 | ✅ | API 契約測試。 |
| FR-A-03 | 系統**應**提供 `POST /v1/upload`、`POST /v1/upload/batch`、`DELETE /v1/documents/{filename}`、`GET /v1/documents`、`POST /v1/reindex`。 | ✅ | API 契約測試。 |
| FR-A-04 | 系統**應**提供無認證 `GET /health` 端點供健康檢查使用。 | ✅ | API 契約測試。 |
| FR-A-05 | 系統**應**支援 CORS，且允許來源透過 `ALLOWED_ORIGINS` 環境變數控制（逗號分隔白名單；預設不應為 `*` 於生產環境）。 | ✅ | CORS pre-flight 整合測試。 |
| FR-A-06 | Streaming 回應**應**遵循 SSE（`text/event-stream`）格式；系統先完整緩衝並過濾模型回答，再送出一個或多個 content `data:` chunk，最後以 `data: [DONE]` 結束。不要求每 token 一個 chunk。 | ✅ | SSE 整合測試：驗證 envelope、過濾後 content 與 `[DONE]`。 |

### 4.3 文件攝取管線（FR-I）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| FR-I-01 | 系統**應**支援 PDF / DOCX / RTF / TXT / MD 五種格式之輸入文件，並轉換為 Markdown 標準中間格式。 | ✅ | 單元測試：每種格式各一個樣本。 |
| FR-I-02 | 系統**應**對上傳檔案計算 SHA-256，若 metadata 已有相同 hash 則回傳 `status: skipped`，避免重複索引（除非顯式 `overwrite=true`）。 | ✅ | 整合測試：同一檔案重複上傳。 |
| FR-I-03 | 系統**應**提供 `scripts/reindex.py`，支援三種模式：全量重建、單檔 upsert、單檔刪除。 | ✅ | CLI 行為測試。 |
| FR-I-04 | 索引管線**應**將文件以階層方式儲存：父文件（完整段落）入 docstore JSON、子 chunk 入 pgvector。 | ✅ | 資料庫狀態檢查。 |

### 4.4 提示詞與回應管理（FR-P）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| FR-P-01 | 所有 Prompt **應**集中於 `rag_system/core/prompts.py`，禁止散佈於各節點中。 | ◐ | Code review + grep 檢查。 |
| FR-P-02 | 系統 Prompt **應**明確限定回答範圍為中文法律領域，並要求引用條文編號。 | ◐ | 人工抽樣 + 黃金資料集引用率指標。 |
| FR-P-03 | 系統**應**在回答中以可解析格式呈現條文引用（如「第X條」），以利後續 `verify_node` 與 `answer_evaluator` 評估。 | ✅ | 正則匹配率 ≥ 0.95（黃金資料集）。 |

### 4.5 部署與維運（FR-O）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| FR-O-01 | 系統**應**完全容器化；內網部署機已載入 10 個核定 images 後，以 `deploy.sh` 啟動全部服務。腳本不得在離線現場 build 或 pull。 | ✅ | 部署契約測試 + 內網 smoke test：缺 image 時拒絕；images 齊備時啟動並檢查 `/health`。 |
| FR-O-02 | 系統**應**提供 `save_images.sh` 將所有映像匯出為 tar 檔，便於離線搬運。交付成品**應**在 `MANIFEST.txt` 記錄 SHA-256。 | ✅ | 打包演練：核對所有 tar、大小與 manifest hash；容量上限由交付媒體另行驗收。 |
| FR-O-03 | 所有設定**應**集中於 `ISO42001RAG/.env`，搬遷時僅需修改 `LLM_HOST` 一項。 | ◐ | 文件審查 + 部署演練。 |
| FR-O-04 | 系統**應**提供版本追蹤腳本 `scripts/version_tracker.py`，支援 `snapshot / diff / verify / list` 四個子命令，並可選 `--backup` 同步輸出 tar.gz。 | ✅ | CLI 行為測試。 |

---

## 5. 非功能需求（Non-Functional Requirements）

### 5.1 安全性需求（NFR-S，對應 ISO 42001 A.8）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| NFR-S-01 | 系統**應**對所有非健康檢查端點執行已配置的存取模式：`API_KEYS` 存在時強制 Bearer Token；驗證失敗回 HTTP 401。受控內網可依 NFR-S-02 明確啟用 intranet mode。 | ✅ | `tests/unit/test_auth.py` |
| NFR-S-02 | 系統**應**提供「內網信任模式」：當 `API_KEYS` 未設定且 `ALLOW_INTRANET_MODE=true` 時，以 Client IP 作為稽核身分。 | ✅ | 認證測試案例。 |
| NFR-S-03 | 系統**應 Fail-Closed**：當 `API_KEYS` 與 `ALLOW_INTRANET_MODE` 皆未設定時，回傳 HTTP 503，**絕不靜默暴露未受保護端點**。 | ✅ | 認證測試案例。 |
| NFR-S-04 | 系統**應**僅對列於 `TRUSTED_PROXIES` 的 immediate peer 信任 `X-Forwarded-For` header，防止 IP 偽造。 | ✅ | 認證測試案例（偽造 XFF）。 |
| NFR-S-05 | 系統**應**對每個 API Key 實施速率限制：預設 60 requests / minute（滑動視窗）；超過回傳 HTTP 429。 | ✅ | 速率限制單元測試。 |
| NFR-S-06 | 系統**應**對使用者輸入執行**七類攻擊偵測**：Prompt Injection、系統資訊探測、SQL Injection、LDAP Injection、SSRF、CSRF、角色切換；命中即拒絕並記錄 `security_alert`。 | ✅ | `tests/evaluation/test_prompt_security.py`（79 cases）。 |
| NFR-S-07 | 系統**應**拒絕長度超過 2000 字元之輸入。 | ✅ | 安全測試案例。 |
| NFR-S-08 | 系統**應**對 LLM 輸出執行敏感資訊遮蔽：連線字串、伺服器路徑、API Key、Base64 Token 等。 | ✅ | 輸出過濾單元測試。 |
| NFR-S-09 | 系統**應**預設啟用 SSL 驗證（`VERIFY_SSL=true`）；該設定**必須可由環境變數覆寫且實際生效**。 | ✅ | Config 載入測試。 |
| NFR-S-10 | 系統**應**將 `ALLOWED_ORIGINS` 限制為白名單；生產環境**不得**設為 `*`。 | ◐ | 部署檢核表（pre-prod gate）。 |

### 5.2 稽核與維運需求（NFR-M，對應 ISO 42001 A.6）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| NFR-M-01 | 系統**應**對每次查詢、拒絕、上傳、reindex、認證事件、安全告警寫入結構化稽核日誌（JSONL，每日滾動）。 | ✅ | 整合測試：呼叫 API 後檢查 `audit_*.jsonl`。 |
| NFR-M-02 | 稽核日誌**應**包含：時間、session_id、event_type、scope_check、model_name、response_time_ms、retrieval_doc_count、citation_count、retry_count、anomaly_flags。 | ✅ | JSON schema 比對。 |
| NFR-M-03 | 系統**應**以滑動視窗實施即時異常偵測：延遲突增（> 2× p95）、拒絕率突升（> 50% 近10筆）、安全事件叢集（≥3）、連續重試（retry_count ≥ 2）。 | ✅ | `tests/unit/test_anomaly_detector.py`（15 cases）。 |
| NFR-M-04 | 系統**應**可由稽核日誌輸出 JSON + Markdown 雙格式之每日／月度稽核摘要報告。 | ✅ | 報告輸出格式檢查。 |
| NFR-M-05 | 系統**應**提供版本追蹤：SHA-256 快照、差異比對、完整性驗證；變更**應**同步寫入 `CHANGELOG.md`，並保留人工「審核簽名」欄位。 | ✅ | 腳本行為測試 + CHANGELOG 格式檢查。 |
| NFR-M-06 | 稽核摘要報告**應**保留至少 24 個月，作為稽核證據。 | ◌ | 維運程序文件。 |

### 5.3 偏誤與公平性需求（NFR-F，對應 ISO 42001 A.5）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| NFR-F-01 | 系統**應**建立成對問題偏誤測試資料集，涵蓋性別、族群、社經地位、身心障礙等面向。 | ✅ | `tests/evaluation/bias_test_dataset.json` |
| NFR-F-02 | 系統**應**提供偏誤評估器：對成對問題比對關鍵字覆蓋率與語意相似度，回報「一致 / 不一致」。 | ✅ | `test_bias_fairness.py`（8 cases）。 |
| NFR-F-03 | 系統**應**保留人工填寫之倫理審查清單（`docs/governance/ETHICS_CHECKLIST.md`），作為 A.5 影響評估書面證據。 | ◌ | 文件審查。 |

### 5.4 驗證與確認需求（NFR-V，對應 ISO 42001 A.9）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| NFR-V-01 | 系統**應**維護黃金資料集（≥ 30 筆），每筆含：query、expected_answer、expected_keywords、expected_articles、expected_docs、category、difficulty。 | ✅ | `golden_dataset.json` 結構與筆數檢查。 |
| NFR-V-02 | 系統**應**提供 V&V 評估器，計算 Hit Rate、Precision@K、MRR、關鍵字覆蓋率、條文引用匹配率、結構完整率。 | ✅ | `test_vv_pipeline.py`（28 cases）。 |
| NFR-V-03 | V&V 通過門檻**應**為：Hit Rate ≥ 0.60、Precision@K ≥ 0.50、MRR ≥ 0.40。 | ✅ | 評估報告 pass/fail 判定。 |
| NFR-V-04 | 系統**應**提供 `scripts/run_vv_evaluation.py`，輸出 JSON + Markdown 雙格式 V&V 報告。 | ✅ | 腳本輸出檢查。 |

### 5.5 效能需求（NFR-P）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| NFR-P-01 | 在標準硬體（內網 GPU；本機 4 vCPU / 8 GB）下，非串流查詢的 P95 延遲**應** ≤ 8 秒。 | ✅ | 負載測試（locust / k6）。 |
| NFR-P-02 | 串流查詢的首 content chunk 延遲目標為 ≤ 2 秒。現行實作為先完整緩衝與過濾再送 SSE，因此此 TTFT 目標**目前不成立，且尚未在部署環境驗證**。 | ◌ | 待安全串流設計改版後執行端到端 SSE 計時；現階段不得列為通過指標。 |
| NFR-P-03 | 在 60 rpm 速率限制下，系統**應**穩定運行不出現記憶體洩漏（24 hr soak test）。 | ◐ | 24 小時壓測。 |

### 5.6 可靠性與可維運需求（NFR-R）

| ID | 需求描述 | 可驗證性 | 驗證方法 |
|---|---|---|---|
| NFR-R-01 | 系統**應**提供完整的回滾程序文件，包含「停服 → 還原 → 驗證 → 重啟 → 記錄」五步驟。 | ◌ | README 文件審查。 |
| NFR-R-02 | 上游服務（Triton / pgvector）短暫不可用時，系統**應**回傳清楚的 5xx 錯誤而非靜默失敗。 | ✅ | 故障注入測試。 |
| NFR-R-03 | 系統**應**將 pgvector data、audit_logs、versions、converted_md 透過 Docker volume 持久化，避免容器重啟後資料遺失。 | ✅ | docker-compose 設定審查。 |

---

## 6. ISO 42001 合規追溯矩陣

| ISO 42001 Annex A 控制項 | 控制要求摘要 | 對應需求 | 對應實作模組 | 書面證據 |
|---|---|---|---|---|
| **A.5.2** AI 影響評估 | 評估對個人 / 群體的潛在影響 | NFR-F-03 | `docs/governance/ETHICS_CHECKLIST.md` | 倫理審查清單（人工填寫） |
| **A.5.3** 偏誤評估 | 評估訓練／檢索資料偏誤 | NFR-F-01, F-02 | `core/bias_evaluator.py` | `test_bias_fairness.py` 測試報告 |
| **A.6.2.4** 生命週期監督 | 持續監督部署後行為 | NFR-M-01..04 | `core/anomaly_detector.py`、`core/audit_logger.py` | `AUDIT_LOG_SCHEMA.md`、`data/audit_logs/audit_*.jsonl` |
| **A.6.2.5** 變更管理 | 受控之變更與版本管理 | NFR-M-05 | `scripts/version_tracker.py`、`CHANGELOG.md` | 快照 + 雜湊驗證紀錄 |
| **A.6.2.8** V&V | 驗證與確認 | NFR-V-01..04 | `core/retrieval_evaluator.py`、`core/answer_evaluator.py` | `data/reports/vv_report_*.md` |
| **A.8.2** 存取控制 | 認證與授權 | NFR-S-01..05 | `core/auth.py`、`core/rate_limiter.py` | `test_auth.py` 測試報告（12 cases） |
| **A.8.3** 輸入安全 | 防範惡意輸入 | NFR-S-06, S-07 | `core/input_sanitizer.py` | `test_prompt_security.py`（79 cases） |
| **A.8.4** 輸出安全 | 防止敏感資料洩漏 | NFR-S-08 | `core/output_filter.py` | 同上 |
| **A.9.2** 使用紀錄 | 完整稽核軌跡 | NFR-M-01, M-02 | `core/audit_logger.py` | `data/audit_logs/audit_*.jsonl` |

> 追溯矩陣涵蓋率：**100%**（所有列出的 A 控制項皆有對應需求、實作與證據）。

---

## 7. 技術可行性評估

### 7.1 技術棧成熟度

| 元件 | 版本 | 成熟度 | 風險 |
|---|---|---|---|
| LangGraph | 0.2.x | 🟡 快速演進中 | API 可能變動 → 需鎖版本 |
| LangChain | 0.3.x | 🟡 同上 | 同上 |
| FastAPI | 0.100+ | 🟢 穩定 | 低 |
| PostgreSQL + pgvector | PG 16 + pgvector 0.7 | 🟢 穩定 | 低 |
| Triton Inference Server | 24.xx | 🟢 NVIDIA 官方支援 | 由推論後端維運團隊維運 |
| nv-embed-v2 | NVIDIA 1.5B | 🟢 公開可下載 | 模型本身為固定資產 |
| openai/gpt-oss-20b | 20B | 🟡 開源權重模型 | 推理品質需 V&V 持續驗證 |

### 7.2 推論後端相依評估

| 依賴 | 影響 | 緩解 |
|---|---|---|
| GPU 主機 / Triton | 🔴 致命 | 提供 `/health` 健康檢查 + 5xx 明確錯誤；運維協議（SLA）約定 |
| pgvector | 🔴 致命 | 容器內建；資料 volume 持久化；版本鎖定 |
| 離線部署 | 🟡 受管理 | `save_images.sh` / `make_update_package.sh` 須在可建置機器先製作完整 images；內網 `deploy.sh` 不 build/pull |

### 7.3 開發工作量估算

| Phase | 模組數 | 估計工時 | 依賴 |
|---|---|---|---|
| Phase 3：Prompt 安全 | 3 新增 + 1 修改 | ~ 5 人日 | 無 |
| Phase 2：存取控制 | 2 新增 + 2 修改 | ~ 4 人日 | Phase 3 完成（共用 audit） |
| Phase 1：異常分析 | 2 新增 + 1 修改 | ~ 3 人日 | Phase 2 |
| Phase 5：V&V | 3 新增 + 1 資料集建立 | ~ 5 人日 | Phase 1 |
| Phase 4：偏誤倫理 | 3 新增 + 1 文件 | ~ 3 人日 | Phase 5 |
| **合計** | — | **~ 20 人日** | — |

---

## 8. 驗證與確認策略

### 8.1 測試分層

| 層 | 工具 | 目標覆蓋率 |
|---|---|---|
| 單元測試 | `pytest` | 核心邏輯 ≥ 80% |
| 整合測試 | `pytest` + FastAPI TestClient | 所有 API 端點 |
| 安全測試 | 自訂 `test_prompt_security.py` | 79 攻擊案例 100% 攔截 |
| V&V 評估 | `run_vv_evaluation.py` | Hit Rate ≥ 0.60、Precision@K ≥ 0.50、MRR ≥ 0.40 |
| 偏誤評估 | `test_bias_fairness.py` | 8 對成對問題一致性 100% |
| 24 hr 壓測 | locust / k6 | 60 rpm 穩定無洩漏 |

### 8.2 預期測試案例總數

| 套件 | 預估案例數 |
|---|---|
| `test_anomaly_detector.py` | 15 |
| `test_auth.py` | 12 |
| `test_prompt_security.py` | 79 |
| `test_bias_fairness.py` | 8 |
| `test_vv_pipeline.py` | 28 |
| **合計** | **142** |

### 8.3 黃金資料集設計

```json
{
  "id": "eval_001",
  "query": "陸海空軍懲罰法第46條規定為何？",
  "expected_answer": "...",
  "expected_keywords": ["停役", "降階"],
  "expected_articles": ["第46條"],
  "expected_docs": ["陸海空軍懲罰法.md#第46條"],
  "category": "single_article | cross_reference | out_of_scope | ambiguous",
  "difficulty": "easy | medium | hard"
}
```

---

## 9. 風險評估

### 9.1 技術風險

| 風險 | 機率 | 影響 | 緩解措施 |
|---|---|---|---|
| LangGraph API 變動破壞工作流 | 中 | 高 | 鎖版本 + Smoke test |
| Embed proxy 與 Triton 介面變更 | 低 | 高 | 雙向契約測試 |
| pgvector 索引效能退化 | 低 | 中 | `check_db_status.py` 巡檢；定期 ANALYZE |
| LLM 幻覺引用不存在條文 | 中 | 高 | `verify_node` + 重試上限 |

### 9.2 合規風險

| 風險 | 機率 | 影響 | 緩解措施 |
|---|---|---|---|
| 認證設定錯誤暴露端點 | 中 | 致命 | **Fail-Closed 設計（NFR-S-03）** |
| 稽核日誌遺失 | 低 | 高 | Volume 持久化 + 24 個月保留 |
| 偏誤評估流於形式 | 中 | 中 | 季度抽查 + 倫理審查清單人工簽署 |
| 版本控管不完整無法回滾 | 低 | 高 | `version_tracker.py --backup` 強制建立 tar.gz |

### 9.3 營運風險

| 風險 | 機率 | 影響 | 緩解措施 |
|---|---|---|---|
| 內網無 Git / GitLab | 高 | 中 | 自包含的 `version_tracker.py` |
| GPU 主機更換造成 `LLM_HOST` 變更 | 中 | 中 | 集中於 `.env`，僅一處需改 |
| 法規修訂未及時 reindex | 高 | 高 | 維運 SOP：法規公報發布 24 hr 內 reindex |

---

## 10. 時程與里程碑

### 10.1 開發路徑（依風險優先序）

```
Pre-0: 清理失效測試（tests/unit/test_sources.py）
   │
   ├─ Phase 3 (Prompt 安全) ─ 最高優先：直接阻擋資料洩漏路徑
   ├─ Phase 2 (存取控制)     ─ 次高：補上所有未受保護端點
   ├─ Phase 1 (異常分析)     ─ 為 Phase 4/5 提供基礎設施
   ├─ Phase 5 (V&V)          ─ 稽核證據價值高
   └─ Phase 4 (偏誤倫理)     ─ 最低：依賴前述設施
```

### 10.2 里程碑

| 里程碑 | 預期產出 | 退出條件（Exit Criteria） |
|---|---|---|
| M1：安全基線 | Phase 3 + Phase 2 完成 | 142 cases 中安全相關（91 cases）全綠 |
| M2：可觀測基線 | Phase 1 完成 | 稽核摘要可正常輸出 JSON+MD |
| M3：V&V 證據 | Phase 5 完成 | golden dataset ≥ 30 筆，V&V 報告達門檻 |
| M4：合規完整 | Phase 4 完成 | 倫理審查清單簽署、偏誤測試全綠 |
| M5：v1.0.0 發佈 | 全套件測試 + 稽核摘要 + V&V 報告 | 完成版本快照與 tar.gz 備份 |

---

## 11. 審查結論與建議

### 11.1 整體評定

<!--
  ┌─────────────────────────────────────────────────────────────┐
  │  ⬇⬇⬇  此段請審查負責人親自填寫（建議 5–10 行）  ⬇⬇⬇        │
  │                                                              │
  │  撰寫要點：                                                  │
  │    1. 一句話結論（建議起手式：「本系統需求規格經審查，      │
  │       評定為『有條件通過』⋯」）                              │
  │    2. 列舉 2-3 個本次規格的最大優點（例：Fail-Closed         │
  │       設計、ISO 42001 追溯完整、V&V 門檻清晰）              │
  │    3. 指出進入下一階段需先解決的條件（指向 §11.2）          │
  │    4. 簽署人姓名與職稱                                      │
  │                                                              │
  │  為什麼這段要你親自寫：                                      │
  │    審查結論承載「審查委員會的意志」，不能由 AI 代筆。       │
  │    這段也是 ISO 稽核員會直接讀的段落，必須有具名負責人。   │
  └─────────────────────────────────────────────────────────────┘
-->

> _（請於此處填入審查結論文字並署名。）_

### 11.2 條件式建議（Conditional Recommendations）

下列 5 項屬於「開發起點即應落實的基線控制」，建議於 Pre-0 / Phase 3 起一併處理；**不構成阻擋性 gate**，但若忽略將於後續 Phase 形成技術債：

| # | 建議 | 對應需求 | 為何在 v0 必須處理 |
|---|---|---|---|
| C-1 | 刪除 `tests/unit/test_sources.py`（引用不存在的 `rag_system.node` 模組與函數，屬死測試）。 | — | 死測試會讓 CI 永遠紅燈，阻礙後續所有測試工作。 |
| C-2 | 修正 `RAGConfig.from_env()` 必須讀取 `VERIFY_SSL` 環境變數，並將預設值改為 `True`。 | NFR-S-09 | 規格寫了預設啟用 SSL，但若 `from_env` 未讀取此變數，等同永遠關閉，與規格矛盾。 |
| C-3 | 在 `api.py` 加入 CORS Middleware，並將 `ALLOWED_ORIGINS` 列為**必填**環境變數；生產環境禁止 `*`。 | FR-A-05、NFR-S-10 | 開發階段若先預設 `*`，事後收斂常被遺漏。 |
| C-4 | 黃金資料集 `tests/evaluation/golden_dataset.json` 自首版即應採完整結構（含 `expected_answer`、`expected_docs`、`category`、`difficulty` 等欄位），且筆數 ≥ 30 後再進入 Phase 5。 | NFR-V-01 | 若資料集筆數不足或欄位殘缺，Hit Rate / MRR 等指標無法計算，V&V 報告將形同空殼，A.9 證據鏈即失效。 |
| C-5 | 所有 API 端點（chat、upload、delete、reindex、list）**必須**從第一個 commit 起就掛上 `Security(get_api_key)` 依賴。 | NFR-S-01 | 認證若採「後補」策略，極可能漏掛端點而靜默暴露。Fail-Closed（NFR-S-03）只能擋住「全無設定」的情境。 |

### 11.3 非阻擋性改善建議（Non-Blocking Suggestions）

| # | 建議 | 動機 |
|---|---|---|
| S-1 | 在黃金資料集中加入 `expected_docs` 後，將 Hit Rate 與 MRR 計算自動納入 CI 流程。 | 防止後續模型／檢索改動使品質回退而無人發現。 |
| S-2 | 對 `anomaly_detector` 之滑動視窗大小（`window=50`）與門檻（`2× p95`、`> 50%`）保留 config 參數化空間，便於後續依正式流量調整。 | 上線初期流量分佈未知，固定門檻可能誤報或漏報。 |
| S-3 | `verify_node` 重試上限 `MAX_RETRIES=2` 建議移至 `RAGConfig`，避免日後微調須改 code。 | 提升維運效率。 |
| S-4 | 稽核摘要除日／月度外，建議再產出「每安全事件**單獨**事件報告」，便於資安單位處理單案。 | 提升 A.6 應變時效。 |
| S-5 | 將 `data/audit_logs/` 改為「append-only 檔案權限 + 定期離線備份」雙重保護。 | 強化 A.9 不可否認性。 |

### 11.4 審查委員會簽署欄

| 角色 | 姓名 | 簽署日期 | 簽名 |
|---|---|---|---|
| 主審 | _（待簽）_ | 2026-05-26 | |
| 資訊安全 | _（待簽）_ | 2026-05-26 | |
| 法務 / 法規 | _（待簽）_ | 2026-05-26 | |
| ISO 42001 內審 | _（待簽）_ | 2026-05-26 | |
| 系統運維 | _（待簽）_ | 2026-05-26 | |

---

## 附錄 A：縮寫與術語

| 縮寫 | 全稱 | 說明 |
|---|---|---|
| RAG | Retrieval-Augmented Generation | 檢索增強生成 |
| BM25 | Best Matching 25 | 經典詞頻關鍵字檢索演算法 |
| MRR | Mean Reciprocal Rank | 平均倒數排名（IR 指標） |
| TTFT | Time To First Token | 傳統 token 串流的首 token 延遲；本系統現行緩衝式 SSE 應改量「首 content chunk」延遲 |
| SSE | Server-Sent Events | 伺服器推送事件（HTTP 串流） |
| V&V | Verification & Validation | 驗證與確認 |
| SRS | Software Requirements Specification | 軟體需求規格書 |
| SLA | Service Level Agreement | 服務水準協議 |
| SOP | Standard Operating Procedure | 標準作業程序 |

## 附錄 B：相關文件

| 文件 | 關係 |
|---|---|
| `README.md` | 系統使用與部署說明（與本報告需求應一致） |
| `plan.md` | ISO 42001 合規實作計畫（Phase 1–5 開發藍圖） |
| `CHANGELOG.md` | 變更紀錄（NFR-M-05 證據鏈） |
| `docs/governance/ETHICS_CHECKLIST.md` | 倫理審查清單（NFR-F-03 證據） |
| `AGENT.md` | 工程設計哲學與 MCP 整合策略 |
