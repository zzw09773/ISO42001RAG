# 系統架構分析（System Architecture Analysis）

> ISO/IEC 42001 條文 4.1（組織脈絡）、6.1（風險與機會）、A.4（AI 系統生命週期資源）、
> A.6.2（AI 系統生命週期）證據。本文件系統性描述 ISO 42001 RAG 法律文件查詢系統
> 的部署架構、AI 處理管線、資料生命週期、安全縱深與稽核機制，每項架構主張附
> **可開檔驗證的 file/symbol 級證據**，並誠實揭露已驗證的已知限制。行號會隨小幅編輯漂移，因此不作為穩定證據鍵。
>
> **編製方法**：本版以檔案逐項核對四個架構面（基礎設施／安全／資料／AI 管線），
> 僅收錄能以 file/symbol 證據支持的主張；覆核時被修正者採正確版本、無法驗證者剔除。
> 覆核覆蓋 74 條架構主張。
>
> **狀態**：內網完整 stack 復核版（2026-07-13）　**系統版本**：v1.1.0 基線
> **權責**：龔修潁負責 RAG 相關後端與稽核日誌證據整理；張丘負責強密碼、憑證與
> OpenWebUI/Keycloak 入口整理。風險接受決定見 `governance/AI_RISK_ASSESSMENT.md`，
> 由稽核負責人評定。

---

## 0. 摘要

本系統是一套中文法律 RAG（檢索增強生成）系統，供國軍法律承辦人查詢軍事法規條文。
目前工作區定位為**外部稽核準備乾淨基線**：保留完整容器服務架構，清空歷史稽核日誌、
部署包、Docker tar、版本 snapshot、舊憑證與本機機密設定。架構特徵：

- **模型外置**：系統本身不含 LLM 與 Embedding 模型，透過環境變數契約連接內網
  GPU 推論伺服器（Triton + vLLM）。系統可被完整稽核的是「編排、檢索、治理」層。
- **AI 管線**：LangGraph 狀態圖,classify → retrieve → generate → verify 四節點,
  含 verify→retrieve 自我修正迴圈;另有預設關閉的 ReAct 代理路徑（feature flag）。
- **縱深防禦**：認證 → 速率限制 → 確定性輸入清洗 → 範疇分類 → 檢索過濾 →
  輸出遮蔽 → 防竄改雜湊鏈稽核,共多層,安全檢查確定性先行、不委派 LLM。
- **完整內網 stack**：Compose project/image 名固定為 `iso42001rag`，含
  `db`、`embed-proxy`、`rag-api`、`openwebui`、`keycloak`、`nginx`、`jupyter`、
  `code-server`、`monitoring`、`admin` 共 10 個服務。`admin:8300` 是憑證卡維運入口。

本系統的證據強項在於**完整的稽核軌跡**（雜湊鏈日誌、prompt 版控、actions 軌跡）。
本文件同時誠實列出已驗證的架構限制（§7），其中
部署組態類（直連服務明文 HTTP、Jupyter 無認證、DB 預設帳密、code-server Docker socket）
影響面最大,均附證據
並對應到風險登錄。

---

## 1. 部署拓撲

### 1.1 容器與職責

`docker-compose.yaml` 的 ISO42001 主系統範圍包含 10 個 services，全部 `restart: unless-stopped`。其中
`db/pgvector` 是 RAG 必要基礎依賴；其餘對應外部稽核準備主要服務：

| 服務 | 角色 | 對宿主埠 | 證據 |
|---|---|---|---|
| `db` (pgvector) | PostgreSQL 17 + pgvector 向量庫；亦存對話歷史 | 15432→5432 | `docker-compose.yaml` `services.db` |
| `embed-proxy` | OpenAI `/v1/embeddings` HTTP ↔ Triton gRPC 轉譯 | 17100→7100 | `docker-compose.yaml` `services.embed-proxy`；`embed_proxy/proxy.py` |
| `rag-api` | FastAPI RAG 主服務（檢索＋生成＋稽核） | 8043→8000 | `docker-compose.yaml` `services.rag-api`；`RAG/api.py` |
| `openwebui` | Open WebUI 0.7.2 使用者聊天前端 | 18088→8080 | `docker-compose.yaml` `services.openwebui` |
| `keycloak` | Keycloak 26.5.6；OpenWebUI OIDC 與強密碼註冊 | 18080→8080 | `docker-compose.yaml` `services.keycloak`；`keycloak/import/iso42001-realm.json` |
| `nginx` | HTTPS 反向代理；外部稽核準備宿主端避開 80/443 | 8088→80, 8443→443 | `docker-compose.yaml` `services.nginx`；`nginx/nginx.conf` |
| `jupyter` | 開發／法規索引環境 | 25678→8888 | `docker-compose.yaml` `services.jupyter` |
| `code-server` | 瀏覽器 IDE，掛載整個 ISO42001 專案，含 container/Docker extension | 18443→8080 | `docker-compose.yaml` `services.code-server`；`code-server/Dockerfile` |
| `monitoring` | 健康、告警、漂移、V&V 與稽核儀表板 | 8200→8200 | `docker-compose.yaml` `services.monitoring`；`monitoring_addon/` |
| `admin` | 憑證卡登入的維運管理台，可執行受限作業與查看報告 | 8300→8300 | `docker-compose.yaml` `services.admin`；`admin_console/` |

### 1.2 已驗證的架構事實

- **模型外置契約**：rag-api、jupyter 的 Embedding 皆經
  `http://embed-proxy:7100/v1`；embed-proxy 連 Triton 的 gRPC 埠由 `EMBED_GRPC_PORT`
  設定。LLM 仍由 `.env` 的 `LLM_API_BASE` 指向推論後端。
- **設定重載邊界**：Admin 寫根 `.env`，但僅同步 13 個非秘密白名單鍵至
  `admin_console/data/rag-runtime.env`；rag-api 唯讀掛載該目錄並在程序啟動前載入。
  `rag-effective.env` 亦僅記錄這 13 鍵的實際生效快照。兩檔都不含 `API_KEYS`、
  `LLM_API_KEY`、`EMBED_API_KEY` 或 Keycloak/code-server/Admin 密碼；但 rag-api 所需的
  `LLM_API_KEY` / `EMBED_API_KEY` 仍由 Compose `services.rag-api.environment` 單獨注入。
- **健康依賴鏈**：rag-api 依賴 db 與 embed-proxy 的 `service_healthy`；openwebui 依賴
  rag-api healthy 與 keycloak started（`docker-compose.yaml` `depends_on`）。
- **完整 stack 部署**：`deploy.sh` 先載入 `images/*.tar`（如存在）並確認 10 個 images
  已就緒，再以 `--no-build --pull never` 啟動。乾淨 checkout 必須先備齊 tar，或在可建置機器執行
  `make_update_package.sh`。Compose `--wait` 會等有 healthcheck 的服務 healthy，對沒有 healthcheck
  的服務只確認 running；它不是端到端 RAG 與上游模型 readiness 證明。
- **TLS 邊界**：nginx 在容器內 listen 80/443，宿主端映射為 8088/8443；HTTP 轉向
  `https://aimla.ai.example.com:8443/`，TLS 反代 OpenWebUI `/`
  （`docker-compose.yaml` `services.nginx`；`nginx/nginx.conf` `server`）。
- **TLS 私鑰邊界**：本次工作樹已將曾被追蹤的 `nginx/ssl/cert.key` / `cert.crt` /
  `cert.csr` 標記刪除，但舊私鑰仍存在 Git history，必須視為已洩漏並於下次部署輪替。
  刪除須納入交付 commit；本次不主張 history 已清理。新憑證由部署機產生或由機密配發流程提供。

> ⚠ **此拓撲的重要後果**：nginx 已提供 OpenWebUI 的 HTTPS 入口，但
> rag-api(8043)、jupyter(25678)、db(15432)、embed-proxy(17100)、keycloak(18080)、
> code-server(18443)、monitoring(8200)、admin(8300) 仍有直連宿主埠。未套用 hardening 或
> 防火牆前，這些直連入口仍需視為部署層風險。詳見 §7 風險 R-INFRA-1/R-INFRA-2/R-INFRA-3。

---

## 2. AI 處理管線

### 2.1 經典工作流（生產預設）

LangGraph `StateGraph`,進入點 `classify`,四路條件路由（`graph.py` `create_rag_workflow`）：

```
classify ──┬─ legal ──────→ retrieve → generate → verify ─┬─ END
           │                              ↑________________│ needs_retry
           ├─ reject ────────────────────────────────────→ END
           ├─ passthrough ──────────────────────────────→ END
           └─ security_block ───────────────────────────→ END
```

| 節點 | 職責 | 證據 |
|---|---|---|
| `classify` | sanitize 確定性先行 → `### Task:` 前綴判定 → LLM JSON 路由 → regex 關鍵字 fallback | `nodes.py` `create_classify_node` |
| `retrieve` | 多階段混合檢索(見 §2.3);例外→空 context 誠實降級 | `nodes.py` `create_retrieve_node` |
| `generate` | 注入 context 生成;retry 時注入 verify feedback;輸出過 filter_output | `nodes.py` `create_generate_node` |
| `verify` | 先執行 deterministic citation provenance gate，再做 regex 結構檢查；失敗回邊 retrieve，MAX_RETRIES=2；額度耗盡仍無據時 fail-safe 取代 | `nodes.py` `create_verify_node`；`graph.py` `create_rag_workflow` |

**稽核軌跡**:`GraphState.actions` 以 `Annotated[list, operator.add]` reducer 累積各節點
動作,最終形成有序軌跡(如 `classify=llm:legal → retrieve(docs=5) → generate(citations=3)
→ verify=passed(regex)`),非串流路徑由 `api.py` 整批寫入稽核日誌（`state.py` `GraphState.actions`；
`api.py` chat completion handler）。
此為 ISO 42001 A.6.2(生命週期軌跡)的核心證據。

### 2.2 verify 節點的真實行為（重要澄清）

生產 classic graph 以 `create_verify_node(llm=None)` 接線，因此不使用 LLM 語意自省。程式碼
保留了 LLM verify 能力但未啟用——這是 v1.2 實驗的數據驅動回退(LLM verify 使
Precision 0.78→0.71 而 Hit Rate 無增益,故回退,決策見 graph.py:97-103 註解)。

現行 verify 在任何 regex 結構判定前，先從回答與 `retrieved_sources` 抽出「第 N 條」。
若回答引用的條號不在檢索來源中，節點回傳 `needs_retry(ungrounded_citation)`；
若到 `MAX_RETRIES` 仍無法對應來源，節點以不含原條號的安全訊息取代回答，並記錄
`verify=failed_safe(ungrounded_citation)`，而不是因重試耗盡而接受無據引用。若回答含條號但
`retrieved_sources` 為空，該條號同樣視為無據；阿拉伯數字與中文數字條號都會核對。更深的「主張是否被條文內容支持」
由離線 RAGAS faithfulness 補充。

### 2.3 檢索層（多階段混合管線）

檢索是六階段混合管線(retrieval.py),非單一向量檢索：

| 階段 | 機制 | 觸發條件 | 證據 |
|---|---|---|---|
| Stage 0 | 條號快速通道:BM25 精準命中直接 pin | 查詢含「第N條」 | retrieval.py:167-237 |
| Stage 0.5 | HyDE:LLM 生成假設文件再檢索 | 查詢**無**條號 | retrieval.py:285-294 |
| Stage 0.75 | Self-Query:LLM 抽法規名→metadata 過濾(加法合併,非限縮) | cross_reference 且抽出 law_names | retrieval.py:303-315,364-410 |
| Stage 1 | BM25 + 向量混合,來源多樣性 round-robin 合併 | 恆 | retrieval.py:455-517 |
| Stage 2 | LLM listwise 重排取 top-N | 恆(pinned 免重排) | retrieval.py:520-600 |
| Stage 3 | 以 doc_id 取回「一條一父文件」全文 | 恆 | retrieval.py:602-632 |

所有 LLM 步驟失敗均 **fail-open** 回退基礎混合結果。條號快速通道使命中條文本身免經
rerank,但同一查詢仍對其餘候選執行 rerank 以挑背景文件。

> **重試迴圈的架構盲點**:verify 失敗回邊 retrieve 時,對同一 question 用相同(確定性)
> 查詢,取回**完全相同**的文件,自我修正只靠 feedback 改寫 generate prompt。換言之,
> 若失敗根因是「檢索沒撈到正確條文」,重試無法補救。詳見 §7 風險 R-PIPE-2。

### 2.4 ReAct 代理路徑（feature flag，預設關閉）

`react_workflow.py` 以 `create_react_agent` 包裝單一檢索工具,LLM 自主決定檢索時機與
次數(理論多跳)。由 `REACT_MODE` 環境變數開關,**預設關閉**,零碼回滾（`graph.py` `get_workflow`）。

**治理狀態(照實揭露)**:此路徑定位為保留的原型(CHANGELOG.md:248),經交叉覆核確認
尚未達完整生產治理標準:
- ReAct 同步與 SSE 已共用 pre-classify、deterministic citation provenance、重試額度耗盡
  fail-safe 與 `filter_output`；SSE 等待最終 post-verify generation 後才送出；
- 稽核軌跡降級:回傳無 actions 鍵 → 稽核記 `actions=[]`（`react_workflow.py` query wrapper；`api.py` audit write）;
- 無明確最大步數,超限拋未捕捉的 HTTP 500（`react_workflow.py` agent invoke）;
- 已有 citation provenance 回歸測試，但未對 ReAct 路徑跑完整 golden dataset gating 評測。

**結論**:現行生產系統(REACT_MODE 關閉)的可稽核性完整;ReAct 路徑的正式啟用須先
通過治理閘(影子評估 + actions schema + 測試),屬稽核後 roadmap。本系統交付狀態
**不依賴**此路徑。

---

## 3. 資料架構與生命週期

### 3.1 資料流

```
法規 Markdown (converted_md/, 2 部法)
  └─reindex.py→ IngestionService「第N條」感知切分
       ├─ parent(每條全文)→ LocalFileStore docstore（外部稽核準備目前已清空，reindex 後重建）
       └─ child chunk(800字/重疊100)→ pgvector「laws_vectors」collection
查詢 → AuditLogger → audit_YYYY-MM-DD.jsonl(雜湊鏈,UTC+8 滾動,0o640；目前乾淨基線無歷史檔)
對話 → conversations 表(同一 PostgreSQL,session_id 隔離)
原始碼/設定/法規 → version_tracker SHA-256 快照（外部稽核準備目前只保留 versions/.gitkeep，正式快照重新產生）
```

### 3.2 已驗證事實

- **Article-Aware Chunking**:以整行正規式「第N條」(支援阿拉伯與中文數字)為界,每條
  一個 parent Document;條文內容不注入前綴(曾致 Hit Rate 0.871→0.806,故移除)
  (ingestion.py:32-35,85-155)。
- **Metadata 溯源**:子 chunk 帶 source/hash/article_id/law_name/doc_id;刪除與稽核皆
  依 metadata 定位(ingestion.py:143-159)。（註:law_name 僅條文 parent 帶,preamble 與
  fallback chunk 不含——交叉覆核修正。）
- **稽核日誌防竄改**:每筆 `entry_hash = SHA256(prev_hash + canonical_json)`,genesis 為
  64 個 0,跨實例共享 `_LAST_HASH` + `threading.Lock` 防鏈分叉(audit_logger.py:108-122)。
- **變更管理(無 Git)**:version_tracker 可對追蹤檔(含法規 md)做 SHA-256 快照,排除
  執行期資料並自動追加 CHANGELOG(version_tracker.py:118-160)。外部稽核準備已刪除舊
  snapshot 與 tar 備份，避免把舊執行期證據混入乾淨內網基線。

### 3.3 資料生命週期的已知缺口

交叉覆核發現多項生命週期終端控制缺失,均列入 §7：
- 稽核日誌 24 個月保留(NFR-M-06)**僅文件宣告,無自動化清理/輪替機制**(R-DATA-1);
- 對話表無 TTL/去識別化,含個資的查詢原文無限期累積(R-DATA-2);
- 文件刪除端點 `DELETE /v1/documents/{filename}` **不寫稽核**(R-DATA-3);
- 雜湊鏈以「每日檔」為錨點,**整日日誌檔遭刪除無法由 verify_integrity 偵測**(R-DATA-4)。

---

## 4. 安全縱深架構

### 4.1 防禦層次（已對程式碼逐層驗證）

| 層 | 機制 | 證據 | ISO 對應 |
|---|---|---|---|
| 網路 | nginx TLS（OpenWebUI `/`）；其他直連埠需靠 hardening/防火牆收斂 | nginx.conf | 27001 A.8.15 |
| 認證 | API key／Intranet mode／503 fail-closed;X-Forwarded-For 僅信任 TRUSTED_PROXIES | `auth.py` `get_api_key` / `get_client_ip` | 42001 A.9 |
| 速率限制 | 每金鑰 60 req/min,超限 429+Retry-After | `rate_limiter.py` `check_rate_limit`；`api.py` chat handler | 42001 A.9 |
| 輸入清洗 | 確定性 regex 威脅攔截，在 LLM 前執行 | input_sanitizer.py | 42001 A.8 |
| 範疇分類 | security 永遠先行,security_block 寫 security_alert | `nodes.py` `create_classify_node` / `security_block_node` | 42001 A.8/A.9 |
| 檢索過濾 | Self-Query metadata 過濾（加法式） | `retrieval.py` `_filtered_vector_search` | 42001 A.7 |
| 輸出遮蔽 | filter_output 6 條敏感樣式規則 | `output_filter.py` `filter_output` | 42001 A.8 |
| 稽核 | SHA-256 雜湊鏈日誌 + verify_integrity | `audit_logger.py` `_write` / `verify_integrity` | 27001 A.5.28/A.8.15 |

### 4.2 核心安全保證（已驗證）

- **安全先行不委派 LLM**:sanitize 在任何 LLM 路由前執行,LLM 故障無法繞過
  （`nodes.py` `create_classify_node`）。
- **Fail-closed 認證**:API_KEYS 未設且未明確啟用 intranet mode 時,受保護端點回 503
  而非靜默放行（`auth.py` `get_api_key`）。現行受控內網可保持 `ALLOW_INTRANET_MODE=true`。
- **來源 IP 防偽**:X-Forwarded-For 僅在直接 TCP peer 屬 TRUSTED_PROXIES 時採信,直連
  客戶端無法偽造稽核來源 IP（`auth.py` `get_client_ip`）。
- **SAFETY_CONTROLS.md 守則對應**:9 道守則中 7 道(認證、速率、輸入清洗、範疇分類、
  Self-Query、輸出過濾、雜湊鏈)可逐一對應程式碼。文件-程式碼一致性已覆核:
  守則⑦(verify)的文件描述「regex 為現行、LLM 為已回退實驗」**與部署一致**(graph.py:104
  傳 llm=None);對話摘要功能確實存在(memory.py `ConversationSummarizer`)。

### 4.3 已驗證的安全缺口

交叉覆核確認以下缺口(均列 §7,部分為高風險):
- API 與 ReAct SSE 都在 post-verify、citation fail-safe 與 `filter_output` 後送出；ReAct
  尚缺完整 actions 稽核與 golden gating（R-SEC-1r）；
- session_id 取自未驗證 header 且未綁 API key,可載入他人對話歷史(R-SEC-2,high);
- CORS 預設 `allow_origins='*'` 且 `allow_credentials=True`(R-SEC-3);
- 速率限制僅掛 chat 端點,upload/documents/reindex 無速率限制(R-SEC-4);
- passthrough 路由繞過輸出過濾(R-SEC-5)。

---

## 5. 稽核與驗證機制

ISO42001 主系統以稽核日誌、版本紀錄與測試結果作為可查核證據。

| 機制 | 目的 | 證據 |
|---|---|---|
| RAG API 系統版號 | 對外顯示主系統基線版號，供 OpenAPI、`/health`、`/v1/models` 查核 | `rag_system/core/version.py`、`api.py` |
| 雜湊鏈稽核日誌 | 追溯每筆查詢、安全事件、認證事件與工作流 actions | `audit_logger.py`、`AUDIT_LOG_SCHEMA.md` |
| Prompt 基線 hash | 將回覆行為對應到當時 `SYSTEM_PROMPT_BASELINE` | `prompts.py`、`PROMPT_VERSIONS.md` |
| 版本快照 | 對原始碼、設定與法規語料產生 SHA-256 版控快照 | `scripts/version_tracker.py`、`RAG/data/versions/` |
| V&V 測試 | 驗證檢索與回答品質是否達成業務目標 | `RAG/tests/evaluation/`、`scripts/run_vv_evaluation.py` |
| 安全測試 | 驗證 prompt injection、惡意輸入與敏感輸出控制 | `RAG/tests/evaluation/test_prompt_security.py` |
| 引用溯源測試 | 驗證無據條文引用觸發重試、有據引用通過 | `RAG/tests/unit/test_verify_grounding.py` |
| 專案整合驗證 | base/hardening Compose、shell 語法、根目錄部署契約、RAG/monitoring/admin 三套 pytest；無 Git metadata 的離線包亦執行 `tests/` | `scripts/verify_project.sh`；`tests/` |

---

## 6. ISO 42001 控制對應總表

| ISO 42001 | 架構證據（本文件章節） | 補充文件 |
|---|---|---|
| 4.1 組織脈絡 | §0 摘要、§1 拓撲 | requirements_review_report.md |
| 6.1 風險與機會 | §7 已知限制 | governance/AI_RISK_ASSESSMENT.md |
| A.4 生命週期資源 | §1 模型外置、§3 資料、governance/MODEL_CARD.md | MODEL_CARD.md |
| A.6.2 生命週期 | §2 AI 管線、actions 軌跡 | CHANGELOG.md |
| A.6.2.4 生命週期監督 | §5 稽核與驗證機制 | AUDIT_LOG_SCHEMA.md |
| A.6.2.5 變更管理 | §3.2 version_tracker、§5 版本快照 | PROMPT_VERSIONS.md |
| A.7 資料治理 | §3 資料架構、metadata 溯源 | AUDIT_LOG_SCHEMA.md |
| A.8 安全 | §4 縱深防禦 | SAFETY_CONTROLS.md |
| A.9 負責任使用 | §4.2 認證、governance/HUMAN_OVERSIGHT.md | HUMAN_OVERSIGHT.md |
| 27001 A.5.28/A.8.15 | §3.2/§4 雜湊鏈、來源 IP | AUDIT_LOG_SCHEMA.md |

---

## 7. 已知限制與風險登錄

> **誠實揭露原則**:以下為交叉覆核確認屬實的架構限制。列出它們不是弱點,而是
> ISO 42001 條文 6.1 要求的風險識別證據。風險等級與接受決定見
> `governance/AI_RISK_ASSESSMENT.md`,由稽核負責人評定;本表僅陳述技術事實。
> 多數高影響項屬**部署組態**,可在不解凍 RAG/ 程式碼的前提下,於部署層處置。

### 7.1 部署組態類（影響面最大，多數可在部署層處置）

> **修正狀態（2026-07-13）**：完整 stack 現為 10 個服務，nginx 已納入預設部署，
> host 端口避開 80/443，Keycloak 與 code-server 已建入 compose。仍需由系統管理者
> 依部署邊界決定是否套用 `docker-compose.hardening.yml`、防火牆或反代策略。

| ID | 限制 | 證據 | 嚴重度 | 修正 |
|---|---|---|---|---|
| R-INFRA-1 | nginx 已提供 OpenWebUI HTTPS 入口，但 base Compose 仍發布多個 direct ports，包含 monitoring 與具特權的 `admin:8300` | docker-compose.yaml ports；nginx/nginx.conf | high | 🔧 套用 hardening，將所有非 nginx direct ports（含 admin）綁定 127.0.0.1 |
| R-INFRA-2 | Jupyter 無認證(空 token/密碼)、容器內可 sudo、rw 掛載 RAG 原始碼、埠 25678 開放——等同未認證程式碼執行入口 | RAG/Dockerfile:22;docker-compose.yaml:155-188 | high | 🔧 強化：套用 `docker-compose.hardening.yml`、設定 Jupyter token，或只在需要索引時啟動 |
| R-INFRA-3 | DB 發布於宿主 15432，若 `.env` 沿用預設帳密，conversations 與向量庫可繞過全部應用層防護直接存取 | docker-compose.yaml:14-21;.env.example:29-33 | high | 🔧 埠綁本機 + `.env` 強密碼 |
| R-INFRA-4 | 主系統 services 無自訂 networks,全落同一 default bridge；code-server/Jupyter 若遭濫用可直連 db、embed-proxy | docker-compose.yaml(全文無 networks) | medium | 📋 拓撲變更建議 |
| R-INFRA-5 | `.env.example` 曾缺失導致部署 bootstrap 斷鏈 | .env.example | medium | ✅ **已修正**（已建 .env.example + gitignore 例外） |
| R-INFRA-6 | code-server 掛載整個專案並掛 Docker socket；若密碼弱或對外暴露，等同可控制本機 Docker 與專案檔案 | docker-compose.yaml:190-208;code-server/Dockerfile | high | 🔧 強密碼、限制來源 IP、必要時移除 Docker socket 或改本機-only |
| R-INFRA-7 | admin 可寫 `.env`、重啟 RAG 並透過 Docker socket 執行白名單作業，權限影響面高 | docker-compose.yaml `admin`；admin_console/admincore/dockerops.py | high | 🔧 憑證卡白名單 + hardening loopback + 跳板/VPN；帳密 fallback 僅作 break-glass |

### 7.2 安全機制類

| ID | 限制 | 證據 | 嚴重度 |
|---|---|---|---|
| R-SEC-1r | API 與 ReAct SSE 皆在完整緩衝、post-verify、citation fail-safe 與 filter_output 後送出；ReAct 殘餘風險為 actions 稽核與完整 golden gating 尚未補齊 | `api.py` stream buffer；`react_workflow.py` `astream_react_query` | medium |
| R-SEC-2 | session_id 取自未驗證 header 且未綁 API key,可指定他人 session 載入其對話歷史 | api.py:155-159,184-199 | high |
| R-SEC-3 | CORS 預設 `allow_origins='*'` + `allow_credentials=True` | api.py:29-36 | medium |
| R-SEC-4 | 速率限制僅掛 chat 端點;upload/documents/reindex 無速率限制 | api.py:145 vs 383,488,539,652 | medium |
| R-SEC-5 | passthrough 路由繞過輸出過濾;LLM classify 可將任意查詢判為 passthrough | nodes.py:212-240 | medium |

### 7.3 AI 管線與資料類

| ID | 限制 | 證據 | 嚴重度 |
|---|---|---|---|
| R-PIPE-1r | citation provenance gate 已阻擋「回答條號不在 retrieved_sources」；但無檢索來源時會跳過溯源，且主張與條文內容的深層對齊仍依賴 RAGAS | nodes.py `create_verify_node`；test_verify_grounding.py | low |
| R-PIPE-1b | 回答含「思考過程」段使 `extract_cited_articles` 掃全文時可能納入推理段列舉的候選條文（非最終引用），扭曲評估端 cited 集。**註：思考過程段本身為刻意的透明度設計（A.9，營運單位要求），非缺陷;修正僅在評估端做 section-aware 抽取,不動 prompt** | prompts.py:69-70（刻意設計）；評估端引用抽取工具 | low（僅評估端） |
| R-PIPE-2 | verify 失敗重試不改變檢索(同查詢同結果),檢索層失敗無法由重試補救 | nodes.py:295-306;graph.py:259 | medium |
| R-PIPE-3 | `prompt_version_hash` 只雜湊版本號登錄表不雜湊 prompt 內文,改字串未 bump 版本則 hash 不變,A.4 溯源依賴人工紀律 | prompts.py:45-46 | medium |
| R-DATA-1 | 24 個月保留僅文件宣告,無自動清理/輪替機制 | grep retention 無命中 | medium |
| R-DATA-2 | 對話表無 TTL/去識別化,含個資查詢原文無限期累積 | conversation_store.py:102-114 | medium |
| R-DATA-3 | 文件刪除端點不寫稽核日誌 | api.py DELETE /v1/documents | medium |
| R-DATA-4 | 雜湊鏈以每日檔為錨點,整日日誌檔遭刪無法由 verify_integrity 偵測 | audit_logger.py:63-66 | medium |

---

## 8. 結論

本系統的核心 AI 治理架構——**多階段檢索 + 自我修正管線 + 完整 actions 稽核軌跡 +
防竄改雜湊鏈日誌 + Prompt 版控 + V&V 測試**——結構完整且每一環節皆有 file/symbol
級證據支撐,可逐項向稽核委員展示。生產交付狀態(ReAct 關閉)的可稽核性不依賴任何
未治理的實驗路徑。

§7 列出的限制中,影響面最大者集中於**部署組態**(直連埠、Jupyter 無認證、DB 預設帳密、
code-server/admin Docker socket),這些**不需解凍 RAG/ 程式碼**即可在部署層收斂——應由系統管理者
依風險評定優先序處置,並更新 `governance/AI_RISK_ASSESSMENT.md` 的接受決定。AI 管線層
的殘餘限制(主張級 grounding、重試不改檢索)屬稽核後的精進 roadmap,已記錄於
`[[project-agentic-rag-review]]` 對應的三波路線圖,不影響當前 Hit Rate 0.9355 達標。

---
*本文件已於 2026-07-13 依 `iso42001rag` 10-service stack、citation provenance gate 與本機驗證入口更新。
龔修潁負責 RAG 相關後端內容，張丘負責強密碼、憑證與 OpenWebUI 入口內容；風險接受決定
由稽核負責人另行評定。*
