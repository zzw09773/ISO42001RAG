# 中文法律 RAG 系統 (Chinese Law RAG System)

一套專為中文法律文件設計的**檢索增強生成（RAG）系統**，基於 **LangGraph**、**PostgreSQL + pgvector** 與 **OpenAI 相容 API** 構建，並符合 **ISO 42001 AI 管理系統標準**。

---

## 目錄

1. [核心功能](#-核心功能)
2. [技術堆疊](#-技術堆疊)
3. [查詢流程](#-查詢流程)
4. [快速開始](#-快速開始容器化部署)
5. [環境變數](#️-環境變數)
6. [API 使用](#-api-使用)
7. [專案結構](#-專案結構)
8. [Scripts 操作手冊](#-scripts-操作手冊)
9. [測試](#-測試)
10. [ISO 42001 合規證據清單](#-iso-42001-合規證據清單)
11. [相關文件](#-相關文件)

---

## 🌟 核心功能

### RAG 檢索與生成
- **條文精確查詢（Article Fast-Path）**：偵測查詢中的條文號碼（如「第46條」），直接以 BM25 精確比對取回，繞過 LLM Reranker，確保指定條文必定出現。支援阿拉伯數字與中文數字。
- **混合檢索（Hybrid Retrieval）**：BM25 關鍵字搜尋 ＋ 向量語意搜尋，結果 Round-Robin 合併。
- **LLM Reranking**：以 LLM 對候選 chunks 重新排名，優先選出語意最相關內容。
- **階層式文件索引（Parent-Child）**：小 chunk 做向量搜尋，命中後取回完整父文件。
- **代理工作流（Agentic Workflow）**：使用 ReAct 代理拆解問題、多次搜尋、綜合回答。
- **智慧記憶（Intelligent Memory）**：混合摘要壓縮長對話，Token-Aware 上下文管理（約 3000 tokens）。
- **OpenAI 相容 API**：標準 `/v1/chat/completions` 端點，可直接對接 Open WebUI 或任何 OpenAI 客戶端。

### ISO 42001 合規功能
- **API 認證（A.8）**：三段式存取控制：① Bearer Token 模式（`API_KEYS` 設定時）、② 內網信任模式（`ALLOW_INTRANET_MODE=true`，以 Client IP 稽核）、③ **Fail-Closed**（兩者皆未設定時回傳 503，絕不靜默暴露未受保護端點）。
- **速率限制（A.8）**：每 API Key 每分鐘 60 次請求（滑動視窗），超過回傳 HTTP 429。
- **輸入消毒（A.8）**：七類攻擊偵測：Prompt Injection、系統資訊探測、SQL Injection、LDAP Injection、SSRF、CSRF、角色切換攻擊；超過 2000 字元自動拒絕。
- **輸出過濾（A.8）**：自動遮蔽連線字串、伺服器路徑、API Key、Base64 Token 等敏感資訊。
- **異常偵測（A.6）**：滑動視窗監控延遲突增、拒絕率突升、安全事件叢集、連續重試。
- **稽核日誌（A.6）**：每日 JSONL 滾動記錄所有查詢、安全事件、認證事件。
- **偏誤評估（A.5）**：成對問題一致性檢查，確保不因用詞差異產生歧視性回答。
- **V&V 評估（A.9）**：黃金資料集評估，量化 Hit Rate、Precision@K、MRR、關鍵字覆蓋率、條文引用匹配率。

---

## 🛠 技術堆疊

| 元件 | 說明 |
|---|---|
| **LangGraph** | Agent 狀態管理與工作流 |
| **LangChain** | LLM / Embeddings 介面 |
| **PostgreSQL + pgvector** | 向量資料庫 |
| **Embed Proxy** | OpenAI Embeddings API ↔ Triton gRPC 轉換層 |
| **FastAPI** | OpenAI 相容 HTTP API |
| **PyMuPDF / python-docx** | PDF / Word 文件解析 |

---

## 🔍 查詢流程

```
用戶問題
   │
   ▼
[API 認證 + 速率限制]  →  失敗 → HTTP 401 / 429
   │ 通過
   ▼
[輸入消毒]  →  偵測到攻擊 → security_block 節點（記錄安全事件 + 回傳拒絕訊息）
   │ 通過
   ▼
Stage 0: 條文號碼偵測？（第X條 / 第X十X條）
   ├─ 是 → BM25 精確掃描 → 直接置頂（跳過 Stage 2 Reranker）
   └─ 否 ↓
Stage 1: Hybrid Search（BM25 + Vector，Top-10）
   │
   ▼
Stage 2: LLM Reranker（選出 Top-3）
   │
   ▼
Stage 3: 取回 Parent Document（完整條文段落）
   │
   ▼
LangGraph Agent → 組合回答
   │
   ▼
[輸出過濾]  →  遮蔽連線字串 / 路徑 / Token → 回傳用戶
```

---

## 🚀 快速開始（容器化部署）

系統已完整容器化，使用 `docker-compose.yaml` 統一管理，**不需要手動安裝 Python 環境**。

### 前置需求
- Docker & Docker Compose
- 外部 GPU 推論伺服器（Triton Inference Server）

### 啟動

```bash
# 在 ISO42001Deploy/ 目錄下
./deploy.sh
```

### 離線部署（內網）

```bash
# 在有網路的機器上打包
./save_images.sh       # 輸出到 images/*.tar（共約 5.3GB）

# 複製整個 ISO42001Deploy/ 到內網後執行
./deploy.sh
```

---

## ⚙️ 環境變數

### 容器化部署

所有設定集中在上層 `ISO42001Deploy/.env`，**無需修改本目錄下任何檔案**。

```ini
# ── 模型連線 ──────────────────────────────────────────
LLM_HOST=<GPU 伺服器 IP>       # 搬遷時唯一需要修改的設定
LLM_PORT=7000                  # LLM HTTP port
EMBED_PORT=9000                # Triton gRPC port（供 embed-proxy 使用）
CHAT_MODEL_NAME=openai/gpt-oss-20b
EMBED_MODEL_NAME=nvidia/nv-embed-v2
LLM_API_KEY=<JWT Token>
EMBED_API_KEY=<JWT Token>

# ── ISO 42001 安全設定 ──────────────────────────────────
API_KEYS=key1,key2             # Bearer Token（逗號分隔）；設定此項則啟用 Token 認證
ALLOW_INTRANET_MODE=false      # true = 無 Token 時以 Client IP 稽核（內網專用）
                               # ⚠️ API_KEYS 與 ALLOW_INTRANET_MODE 兩者皆未設定 → HTTP 503（Fail-Closed）
TRUSTED_PROXIES=127.0.0.1     # 信任的 Proxy IP（逗號分隔），用於 X-Forwarded-For 驗證
                               # 僅限此清單內的 Peer 可傳遞真實 Client IP，防止 IP 偽造
ALLOWED_ORIGINS=https://your-ui.internal  # CORS 白名單（逗號分隔，* 代表全開）
VERIFY_SSL=true                # 上游 SSL 驗證（預設 true，測試環境可設 false）
RATE_LIMIT_PER_MINUTE=60       # 每 Key 每分鐘最大請求數（預設 60）
```

**Embedding 連線架構**：

```
RAG API → embed-proxy:8100 (HTTP/OpenAI格式)
              └→ Triton:9001 (gRPC)
```

> Triton 使用 gRPC 協定，`embed-proxy` 負責格式轉換，RAG API 只看到標準 OpenAI 介面。

### 本機開發環境（`RAG/.env`）

```ini
PGVECTOR_URL=postgresql://postgres:postgres@localhost:15432/Judge
EMBED_API_BASE=http://localhost:8100/v1
EMBED_API_KEY=<Token>
EMBED_MODEL_NAME=nvidia/nv-embed-v2
LLM_API_BASE=http://<GPU IP>:7000/v1
CHAT_MODEL_NAME=openai/gpt-oss-20b
TOP_K=5
CHUNK_SIZE=1000
API_KEYS=dev-key-1234          # 開發用 Token；或留空並設 ALLOW_INTRANET_MODE=true
ALLOW_INTRANET_MODE=true       # 本機開發時可開啟，省去 Token（切勿用於生產）
TRUSTED_PROXIES=127.0.0.1     # 本機 nginx proxy IP
VERIFY_SSL=false               # 本機測試時可關閉
```

---

## 📚 API 使用

### API 端點總覽

| 方法 | 路徑 | 說明 | 需要認證 |
|------|------|------|----------|
| `GET` | `/health` | 健康檢查 | 否 |
| `GET` | `/v1/models` | 列出可用模型 | 否 |
| `POST` | `/v1/chat/completions` | 主要問答端點 | **是** |
| `POST` | `/v1/upload` | 上傳文件並索引 | **是** |

### Python 範例

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-api-key",   # 對應 API_KEYS 環境變數
)

response = client.chat.completions.create(
    model="rag-agent",
    messages=[
        {"role": "user", "content": "陸海空軍懲罰法第46條的規定是什麼？"}
    ]
)
print(response.choices[0].message.content)
```

### curl 範例

```bash
# 一般查詢
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "rag-agent",
    "messages": [{"role": "user", "content": "陸海空軍懲罰法第46條"}]
  }'

# 健康檢查（無需認證）
curl http://localhost:8000/health
```

### Session 持續對話

加入 `x-session-id` header 可保留同一對話的上下文記憶：

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-api-key" \
  -H "x-session-id: user-abc-session-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"rag-agent","messages":[{"role":"user","content":"繼續說明第47條"}]}'
```

### CLI 指令（容器內）

```bash
# 單次查詢
python -m rag_system.cli query "陸海空軍懲罰法第46條"

# 僅執行檢索（除錯用，不呼叫 LLM）
python -m rag_system.cli retrieve "第46條"

# 啟動 HTTP Server
python -m rag_system.cli serve --port 8080
```

---

## 📁 專案結構

```
RAG/
├── api.py                          # FastAPI 進入點（認證、速率限制、CORS、路由）
├── CHANGELOG.md                    # 版本變更紀錄（ISO 42001 A.6/A.9 稽核證據）
├── Dockerfile                      # Jupyter 開發環境映像
├── Dockerfile.api                  # RAG API 生產環境映像
├── requirements.txt                # Python 套件清單
│
├── data/
│   ├── converted_md/               # ★ 來源法規 Markdown（在此新增文件後執行 reindex）
│   ├── processed/
│   │   └── docstore/               # Parent Document 快取（JSON，由 reindex.py 產生）
│   ├── audit_logs/                 # 稽核日誌（JSONL，每日滾動）
│   │   └── audit_YYYY-MM-DD.jsonl
│   ├── reports/                    # 監控報告 / V&V 評估報告（由 scripts 產生）
│   │   ├── monitoring_YYYY-MM-DD.json / .md
│   │   └── vv_report_YYYY-MM-DD.json / .md
│   └── versions/                   # 版本快照與備份（由 version_tracker.py 產生）
│       ├── snapshot_YYYY-MM-DD_HHMMSS.json
│       └── backup_YYYY-MM-DD_HHMMSS.tar.gz
│
├── docs/
│   └── governance/
│       └── ETHICS_CHECKLIST.md     # ISO 42001 A.5 倫理審查清單（人工填寫）
│
├── rag_system/
│   ├── core/
│   │   ├── config.py               # 全域設定（TOP_K, CHUNK_SIZE, SSL 等）
│   │   ├── factory.py              # 元件工廠（LLM / DB / Embeddings）
│   │   ├── prompts.py              # Prompt 集中管理（Rerank / Agent）
│   │   ├── auth.py                 # Bearer Token 認證 / 內網 IP 模式
│   │   ├── rate_limiter.py         # 速率限制（每 Key / 分鐘，滑動視窗）
│   │   ├── input_sanitizer.py      # 六類攻擊防禦（Prompt Injection / 系統探測 / SQL Injection / SSRF / CSRF / 角色切換）
│   │   ├── output_filter.py        # 敏感資訊遮蔽（連線字串、路徑、Token）
│   │   ├── anomaly_detector.py     # 即時異常偵測（延遲、拒絕率、安全叢集）
│   │   ├── audit_logger.py         # 日誌記錄（查詢 / 安全告警 / 認證事件）
│   │   ├── bias_evaluator.py       # 偏誤一致性評估（成對問題比較）
│   │   ├── retrieval_evaluator.py  # 檢索準確度指標（Hit Rate, Precision@K, MRR）
│   │   └── answer_evaluator.py     # 回答正確性評估（關鍵字覆蓋、條文引用、結構）
│   ├── services/
│   │   ├── ingestion.py            # 文件索引（切割、向量化、寫入 pgvector）
│   │   ├── retrieval.py            # 混合檢索（BM25 + Vector + 條文快速路徑）
│   │   └── converter.py            # PDF / DOCX / RTF → Markdown 轉換
│   └── agent/
│       ├── graph.py                # LangGraph 工作流（classify / retrieve / generate / verify / reject / security_block / passthrough）
│       ├── nodes.py                # 各 Agent 節點邏輯（classify / retrieve / generate）
│       ├── memory.py               # 對話記憶管理（摘要壓縮 + Token-Aware）
│       └── tools/
│           └── retrieve.py         # Agent 搜尋工具
│
├── scripts/                        # ★ 操作工具（詳見下方說明）
│   ├── reindex.py                  # 知識庫索引管理
│   ├── generate_monitoring_report.py  # ISO 42001 監控報告產生
│   ├── run_vv_evaluation.py        # V&V 驗證評估
│   ├── version_tracker.py          # 版本追蹤（無需 Git）
│   └── debug/                      # 除錯工具（開發用）
│       ├── check_db_status.py      # 查看 pgvector 資料庫狀態
│       ├── clear_data.py           # 清空資料庫（保留結構）
│       └── verify_query.py         # 手動驗證查詢結果
│
└── tests/
    ├── unit/
    │   ├── test_anomaly_detector.py    # 異常偵測（15 cases）
    │   └── test_auth.py                # 認證 / 速率限制（12 cases）
    └── evaluation/
        ├── test_prompt_security.py     # Prompt injection 安全（79 cases）
        ├── test_bias_fairness.py       # 偏誤公平性（8 cases）
        ├── test_vv_pipeline.py         # V&V 管線指標（28 cases）
        ├── golden_dataset.json         # V&V 黃金資料集（30 筆）
        └── bias_test_dataset.json      # 偏誤測試資料集
```

---

## 📜 Scripts 操作手冊

> 所有 scripts 需在 **`ISO42001_jupyter` 容器內**執行，或在已安裝所有相依套件的環境中執行。
> 容器內工作目錄為 `/home/jovyan/work`，對應本機的 `RAG/` 目錄。

---

### `scripts/reindex.py` — 知識庫索引管理

管理 pgvector 向量資料庫中的文件索引。新增法規文件後必須執行。

#### 全量重建（清空後重建所有文件）

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/reindex.py
```

> ⚠️ 會清空現有全部索引，掃描 `data/converted_md/` 下所有 `.md` 檔重建。耗時較長。

#### 新增 / 更新單一文件（推薦）

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/reindex.py --file data/converted_md/陸海空軍懲罰法.md
```

> 等同 upsert：先刪除該文件舊索引，再重新建立。**不影響其他文件**。

#### 刪除指定文件的索引

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/reindex.py --delete 陸海空軍懲罰法.md
```

#### 新增法規的完整流程

```
1. 將法規 RTF / DOCX / PDF 放入 data/input/
2. 使用系統的 /v1/upload API 上傳（自動轉換 + 索引），或：
   a. 手動轉換為 Markdown → 存至 data/converted_md/
   b. 執行 reindex.py --file <新檔案>
3. 驗證：python scripts/debug/check_db_status.py
```

---

### `scripts/generate_monitoring_report.py` — ISO 42001 A.6 監控報告

讀取所有稽核日誌，產生統計報告，作為 **ISO 42001 A.6 監控稽核的書面證據**。

#### 執行

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/generate_monitoring_report.py
```

#### 自訂路徑

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/generate_monitoring_report.py \
  --log-dir data/audit_logs \
  --output-dir data/reports
```

#### 輸出檔案

| 檔案 | 說明 |
|------|------|
| `data/reports/monitoring_YYYY-MM-DD.json` | 機器可讀格式（完整原始數據） |
| `data/reports/monitoring_YYYY-MM-DD.md` | 人可讀格式（稽核呈交用） |

#### 報告內容

- **彙總統計**：總事件數、查詢次數、拒絕次數、拒絕率、安全告警次數、異常事件數、平均延遲
- **異常旗標彙總**：延遲突增 / 拒絕率突升 / 安全事件叢集 / 連續重試 各發生次數
- **每日明細**：每個日誌檔的獨立統計（含 P95 延遲）

#### 稽核頻率建議

| 情境 | 建議頻率 |
|------|----------|
| 日常監控 | 每週一次 |
| ISO 42001 稽核準備 | 每月一次，報告存檔 |
| 安全事件後 | 立即執行 |

---

### `scripts/run_vv_evaluation.py` — ISO 42001 A.9 V&V 評估

對黃金資料集執行離線驗證與確認（Verification & Validation），作為 **ISO 42001 A.9 書面證據**。

> **注意**：本腳本為「離線評估」，不呼叫 LLM。使用 `golden_dataset.json` 中的 `expected_answer` 自評答案品質；檢索指標需填入 `expected_docs` 後才會計算。

#### 執行

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/run_vv_evaluation.py
```

#### 自訂資料集

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/run_vv_evaluation.py \
  --dataset tests/evaluation/golden_dataset.json \
  --output-dir data/reports
```

#### 輸出檔案

| 檔案 | 說明 |
|------|------|
| `data/reports/vv_report_YYYY-MM-DD.json` | 機器可讀格式 |
| `data/reports/vv_report_YYYY-MM-DD.md` | 人可讀格式（稽核呈交用） |

#### 評估指標與通過門檻

| 指標 | 說明 | 通過門檻 |
|------|------|----------|
| **Hit Rate** | 正確文件出現在檢索結果中的比率 | ≥ 0.60 |
| **Precision@K** | Top-K 中正確文件的比率 | ≥ 0.50 |
| **MRR** | 第一個正確文件的排名倒數平均 | ≥ 0.40 |
| **關鍵字覆蓋率** | 回答中出現預期關鍵字的比率 | 參考用 |
| **條文引用匹配** | 回答引用正確條文的比率 | 參考用 |
| **結構完整率** | 回答包含必要結構的比率 | 參考用 |

> 若 `expected_docs` 全部為空（尚未填入檢索 ground truth），檢索指標自動視為通過。

#### 擴充黃金資料集

編輯 `tests/evaluation/golden_dataset.json`，每筆格式如下：

```json
{
  "query": "陸海空軍懲罰法第46條規定為何？",
  "expected_answer": "第46條規定……",
  "expected_keywords": ["停役", "降階"],
  "expected_articles": ["第46條"],
  "expected_docs": ["陸海空軍懲罰法.md#第46條"],
  "category": "single_article",
  "difficulty": "easy"
}
```

| 欄位 | 必填 | 說明 |
|------|------|------|
| `query` | ✅ | 測試問題 |
| `expected_answer` | ✅ | 預期回答（用於自評） |
| `expected_keywords` | ✅ | 預期出現的關鍵字列表 |
| `expected_articles` | ✅ | 預期引用的條文列表 |
| `expected_docs` | ❌ | 預期檢索到的文件 ID（填入後才計算 Hit Rate / MRR） |
| `category` | ✅ | `single_article` / `cross_reference` / `out_of_scope` / `ambiguous` |
| `difficulty` | ✅ | `easy` / `medium` / `hard` |

---

### `scripts/version_tracker.py` — 版本追蹤（無 Git 環境）

在沒有 Git / GitLab 的內網環境中，提供 SHA-256 快照式版本管理，作為 **ISO 42001 A.6/A.9 變更管理的書面證據**。

#### `snapshot` — 建立版本快照

```bash
python3 scripts/version_tracker.py snapshot \
  -m "說明此次變更內容" \
  -o "操作者姓名" \
  -v "v1.2.0"
```

加上 `--backup` 同時產生 `tar.gz` 備份（可用於回滾）：

```bash
python3 scripts/version_tracker.py snapshot \
  -m "ISO 42001 合規實作完成" -o "開發團隊" -v "v1.1.0" --backup
```

| 參數 | 說明 |
|------|------|
| `-m` / `--message` | 變更說明（會記入 CHANGELOG） |
| `-o` / `--operator` | 操作者姓名（稽核用） |
| `-v` / `--version` | 版本號（如 v1.2.0） |
| `--backup` | 同時產生 `.tar.gz` 備份存檔 |

> 執行後若偵測到變更，自動追加至 `CHANGELOG.md`（含「審核簽名」欄位供人工填寫）。

#### `diff` — 查看未提交的變更

```bash
# 與最新快照比較
python3 scripts/version_tracker.py diff

# 與指定快照比較
python3 scripts/version_tracker.py diff \
  --base data/versions/snapshot_2026-04-09_063600.json
```

輸出範例：
```
✏️  修改（2 檔）：
  ~ rag_system/core/auth.py
  ~ README.md

合計：+0 ~2 -0
```

#### `verify` — 完整性驗證

比對目前所有檔案的 SHA-256 與快照是否一致，用於**稽核時確認程式碼未被竄改**：

```bash
# 驗證與最新快照是否一致
python3 scripts/version_tracker.py verify

# 驗證與指定快照是否一致
python3 scripts/version_tracker.py verify \
  --base data/versions/snapshot_2026-04-09_063600.json
```

輸出範例：
```
✅ 完整性驗證通過：所有檔案與快照一致。
# 或
❌ 完整性驗證失敗（2 個問題）：
  ⚠️  雜湊不符：rag_system/core/auth.py
  ❌ 檔案遺失：scripts/old_script.py
```

#### `list` — 列出所有快照

```bash
python3 scripts/version_tracker.py list
```

輸出範例：
```
共 3 個快照：

  snapshot_2026-04-09_063600.json  [v1.1.0] 2026-04-09 06:36:00  (60 檔)  ISO 42001 合規實作完成
  snapshot_2026-04-09_063654.json  [v1.1.1] 2026-04-09 06:36:54  (61 檔)  新增版本追蹤工具
  snapshot_2026-04-09_071451.json  [v1.1.2] 2026-04-09 07:14:51  (61 檔)  auth.py 升級內網模式
```

#### 建議的版本管理流程

```
程式碼修改完成
       ↓
執行全部測試（pytest）
       ↓
python3 scripts/version_tracker.py diff      # 確認變更範圍
       ↓
python3 scripts/version_tracker.py snapshot -m "說明" -o "操作者" -v "版本號" --backup
       ↓
在 CHANGELOG.md 中填寫「審核簽名」欄位
```

#### 回滾程序（版本還原）

當部署出現問題需要還原至前一個穩定版本時，依照下列步驟操作。

**前提：** 目標版本必須曾以 `--backup` 建立過 `.tar.gz` 備份，才能完整還原檔案。

---

**步驟 1：確認可用的快照與備份**

```bash
# 列出所有快照
docker exec ISO42001_jupyter python3 scripts/version_tracker.py list

# 輸出範例：
#   snapshot_2026-04-09_063600.json  [v1.1.0]  ISO 42001 合規實作完成
#   snapshot_2026-04-09_071451.json  [v1.1.2]  auth.py 升級
#
# 確認對應的備份存在：
ls data/versions/backup_*.tar.gz
```

**步驟 2：停止 API 服務**

```bash
cd ISO42001Deploy
docker compose stop rag-api
```

**步驟 3：還原檔案**

```bash
# 解壓備份（以 v1.1.0 為例，覆蓋當前檔案）
tar xzf RAG/data/versions/backup_2026-04-09_063600.tar.gz -C RAG/

# 確認還原結果與快照一致
docker exec ISO42001_jupyter python3 scripts/version_tracker.py verify \
  --base data/versions/snapshot_2026-04-09_063600.json
# 應輸出：✅ 完整性驗證通過
```

**步驟 4：重建並重啟服務**

```bash
# 重新 build（套件版本可能也回到舊版）
docker compose up -d --build rag-api

# 確認服務正常
curl http://localhost:8043/health
```

**步驟 5：記錄回滾事件（ISO 42001 稽核要求）**

```bash
docker exec ISO42001_jupyter python3 scripts/version_tracker.py snapshot \
  -m "回滾至 v1.1.0：[說明問題原因]" \
  -o "操作者姓名" \
  -v "v1.1.0-rollback"
```

> 回滾後在 `CHANGELOG.md` 的「審核簽名」欄位填寫負責人，作為 ISO 42001 A.9 變更管理的書面記錄。

---

**無備份時的替代方案（僅程式碼）**

若未使用 `--backup` 建立備份，只能透過快照的 `diff` 找出哪些檔案被修改，再手動還原：

```bash
# 查看目前與目標版本的差異
docker exec ISO42001_jupyter python3 scripts/version_tracker.py diff \
  --base data/versions/snapshot_2026-04-09_063600.json

# 手動將有問題的檔案還原後，重建服務
docker compose up -d --build rag-api
```

---

### `scripts/debug/` — 除錯工具（開發用）

> 以下工具僅供開發除錯，不作為正式操作流程。

#### `check_db_status.py` — 查看資料庫狀態

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/debug/check_db_status.py
```

顯示 pgvector 中各資料表的筆數、儲存大小、索引狀態。

#### `clear_data.py` — 清空資料庫（保留結構）

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/debug/clear_data.py
```

> ⚠️ **危險操作**：清空所有向量資料與 docstore，執行前請確認。清空後需重新執行 `reindex.py` 重建索引。

#### `verify_query.py` — 手動驗證查詢結果

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  python scripts/debug/verify_query.py
```

直接執行 RAG 查詢並顯示完整的檢索過程與中間結果，用於除錯。

---

## 🧪 測試

所有測試需在 **`ISO42001_jupyter` 容器內**執行（host 環境缺少 `langchain_classic` 等相依套件）。

### 執行全部測試

```bash
docker exec -w /home/jovyan/work ISO42001_jupyter \
  /opt/conda/bin/python -m pytest tests/ -v
```

### 執行特定測試模組

```bash
# 安全性測試（Prompt injection）
docker exec -w /home/jovyan/work ISO42001_jupyter \
  /opt/conda/bin/python -m pytest tests/evaluation/test_prompt_security.py -v

# 認證與速率限制
docker exec -w /home/jovyan/work ISO42001_jupyter \
  /opt/conda/bin/python -m pytest tests/unit/test_auth.py -v

# 異常偵測
docker exec -w /home/jovyan/work ISO42001_jupyter \
  /opt/conda/bin/python -m pytest tests/unit/test_anomaly_detector.py -v

# 偏誤公平性
docker exec -w /home/jovyan/work ISO42001_jupyter \
  /opt/conda/bin/python -m pytest tests/evaluation/test_bias_fairness.py -v

# V&V 管線
docker exec -w /home/jovyan/work ISO42001_jupyter \
  /opt/conda/bin/python -m pytest tests/evaluation/test_vv_pipeline.py -v
```

### 測試涵蓋範圍

| 測試檔案 | Cases | 測試對象 |
|----------|-------|----------|
| `test_anomaly_detector.py` | 15 | 延遲突增、拒絕率突升、安全叢集、連續重試偵測、日誌分析 |
| `test_auth.py` | 12 | Bearer Token 驗證、Fail-Closed（503）、內網 IP 模式、TRUSTED_PROXIES 防偽造、速率限制 |
| `test_prompt_security.py` | 79 | Prompt injection（中英文）、系統探測、SQL Injection、LDAP Injection、SSRF、CSRF、角色切換、長度限制、輸出過濾 |
| `test_bias_fairness.py` | 8 | 成對問題一致性、中性回答評分、語意相似度 |
| `test_vv_pipeline.py` | 28 | Hit Rate / Precision@K / MRR 計算、答案評分、資料集載入 |
| **合計** | **142** | |

---

## 📋 ISO 42001 合規證據清單

| 條款 | 要求 | 對應實作 | 書面證據位置 |
|------|------|----------|-------------|
| **A.5** | 倫理審查 | `bias_evaluator.py` + 測試 | `docs/governance/ETHICS_CHECKLIST.md` |
| **A.5** | 偏誤評估 | `test_bias_fairness.py`（8 cases）| 測試報告（pytest -v） |
| **A.6** | 系統監控 | `anomaly_detector.py` + `audit_logger.py` | `data/reports/monitoring_*.md` |
| **A.6** | 稽核日誌 | JSONL 每日滾動 | `data/audit_logs/audit_*.jsonl` |
| **A.6** | 變更管理 | `version_tracker.py` + `CHANGELOG.md` | `data/versions/snapshot_*.json` |
| **A.8** | 存取控制 | `auth.py` + `rate_limiter.py` | `test_auth.py` 測試報告 |
| **A.8** | 輸入安全 | `input_sanitizer.py` | `test_prompt_security.py` 測試報告 |
| **A.8** | 輸出安全 | `output_filter.py` | `test_prompt_security.py` 測試報告 |
| **A.9** | 驗證與確認 | `run_vv_evaluation.py` + 黃金資料集 | `data/reports/vv_report_*.md` |

---

## 📖 相關文件

| 文件 | 說明 |
|---|---|
| `AGENT.md` | 核心工程哲學與 Agent 設計說明 |
| `CHANGELOG.md` | 版本變更紀錄（ISO 42001 A.6/A.9 稽核證據） |
| `docs/governance/ETHICS_CHECKLIST.md` | ISO 42001 A.5 倫理審查清單（人工填寫） |
| `plan.md` | ISO 42001 合規實作計畫（完整紀錄） |
