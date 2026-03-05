# 中文法律 RAG 系統 (Chinese Law RAG System)

這是一個專為中文法律文件設計的檢索增強生成 (RAG) 系統，基於 **LangGraph**、**PostgreSQL (pgvector)** 與 **OpenAI 相容 API** 構建。

本系統利用階層式文件索引（條文/段落）與代理工作流 (Agentic Workflow)，確保回答能夠精確地以具體法律條文為依據。

## 🌟 核心功能

- **條文精確查詢 (Article Fast-Path)**：偵測查詢中的條文號碼（如「第46條」），直接以 BM25 精確比對取回，**繞過 LLM Reranker**，確保指定條文必定出現在回答中。支援阿拉伯數字（第46條）與中文數字（第四十六條）。
- **混合檢索 (Hybrid Retrieval)**：BM25 關鍵字搜尋 ＋ 向量語意搜尋，結果 Round-Robin 合併，兼顧精確與語意。
- **LLM Reranking**：非條文精確查詢時，以 LLM 對候選 chunks 重新排名，確保語意最相關的內容優先。
- **階層式文件索引 (Parent-Child)**：小 chunk 做向量搜尋，命中後取回完整父文件，兼顧搜尋精度與回答完整度。
- **代理工作流 (Agentic Workflow)**：使用 ReAct 代理拆解問題、多次搜尋、綜合回答。
- **智慧記憶 (Intelligent Memory)**：混合摘要壓縮長對話、Token-Aware 上下文管理（保留約 3000 tokens）。
- **OpenAI 相容 API**：標準 `/v1/chat/completions` 端點，可直接對接 Open WebUI 或任何 OpenAI 客戶端。

## 🛠 技術堆疊

| 元件 | 說明 |
|---|---|
| **LangGraph** | Agent 狀態管理與工作流 |
| **LangChain** | LLM / Embeddings 介面 |
| **PostgreSQL + pgvector** | 向量資料庫 |
| **Embed Proxy** | OpenAI Embeddings API ↔ Triton gRPC 轉換層 |
| **FastAPI** | OpenAI 相容 HTTP API |
| **PyMuPDF / python-docx** | PDF / Word 文件解析 |

## 🔍 查詢流程

```
用戶問題
   │
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
```

## 🚀 快速開始（容器化部署）

系統已完整容器化，使用上層 `docker-compose.yaml` 統一管理，**不需要手動安裝 Python 環境**。

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
# 有網路機器上打包
./save_images.sh       # 輸出到 images/*.tar（共約 5.3GB）

# 複製整個 ISO42001Deploy/ 到內網後執行
./deploy.sh
```

---

## ⚙️ 環境變數與服務連線

### 容器化部署（正常使用）

所有設定集中在上層 `ISO42001Deploy/.env`，**無需修改本目錄下的任何檔案**。

```ini
LLM_HOST=<GPU 伺服器 IP>   # 唯一需要在搬遷時修改的設定
LLM_PORT=7000              # LLM HTTP port
EMBED_PORT=9000            # Triton gRPC port（供 embed-proxy 使用）
CHAT_MODEL_NAME=openai/gpt-oss-20b
EMBED_MODEL_NAME=nvidia/nv-embed-v2
LLM_API_KEY=<JWT Token>
EMBED_API_KEY=<JWT Token>
```

**Embedding 連線架構**：

```
RAG API → embed-proxy:8100 (HTTP/OpenAI格式)
              └→ Triton:9001 (gRPC)
```

Triton 的 Embedding 端點使用 **gRPC** 協定，`embed-proxy` 負責將 OpenAI `/v1/embeddings` 格式轉換為 Triton gRPC 呼叫。RAG API 本身只看到標準 OpenAI 格式，無需感知 gRPC 細節。

### 本機開發環境（`.env` in `RAG/`）

在 `RAG/` 目錄下建立 `.env`（參考 `.env.example`）：

```ini
PGVECTOR_URL=postgresql://postgres:postgres@localhost:15432/Judge
EMBED_API_BASE=http://localhost:8100/v1   # 若有啟動 embed-proxy
EMBED_API_KEY=<Token>
EMBED_MODEL_NAME=nvidia/nv-embed-v2
LLM_API_BASE=http://<GPU IP>:7000/v1
CHAT_MODEL_NAME=openai/gpt-oss-20b
TOP_K=5
CHUNK_SIZE=1000
```



## 📚 API 使用

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="dummy")

response = client.chat.completions.create(
    model="rag-agent",
    messages=[
        {"role": "user", "content": "陸海空軍懲罰法第46條的規定是什麼？"}
    ]
)
print(response.choices[0].message.content)
```

### CLI 指令（在容器內或開發環境）

```bash
# 單次查詢
python -m rag_system.cli query "陸海空軍懲罰法第46條"

# 僅執行檢索（除錯用）
python -m rag_system.cli retrieve "第46條"

# 啟動 HTTP Server
python -m rag_system.cli serve --port 8080
```

## 📁 專案結構

```
RAG/
├── api.py                  # FastAPI 進入點
├── Dockerfile              # Jupyter 開發環境
├── Dockerfile.api          # RAG API 生產環境
├── data/
│   ├── converted_md/       # 來源法規 Markdown 檔案（在此新增文件）
│   ├── processed/
│   │   └── docstore/       # Parent Document 儲存（JSON）
│   └── audit_logs/         # 查詢 Audit Log（JSONL）
├── rag_system/
│   ├── core/
│   │   ├── config.py       # 全域設定常數（TOP_K, CHUNK_SIZE 等）
│   │   ├── factory.py      # 元件工廠（LLM / DB / Embeddings）
│   │   └── prompts.py      # Prompt 集中管理（Rerank / Agent）
│   ├── services/
│   │   ├── ingestion.py    # 文件索引服務（切割 + 向量化）
│   │   ├── retrieval.py    # 混合檢索服務（含條文快速路徑）
│   │   └── converter.py    # PDF/DOCX → Markdown 轉換
│   └── agent/
│       ├── graph.py        # LangGraph 工作流定義
│       ├── nodes.py        # Agent 節點邏輯
│       ├── memory.py       # 對話記憶管理
│       └── tools/          # Agent 工具（搜尋等）
└── scripts/
    └── reindex.py          # 重建索引腳本
```

## 📄 管理知識庫文件

### 新增 / 更新單一文件（推薦）

```bash
# 不影響其他文件，直接更新指定檔案的索引
python scripts/reindex.py --file data/converted_md/陸海空軍懲罰法.md
python scripts/reindex.py -f /絕對路徑/新法規.md
```

### 全量重建（清空後重建所有文件）

```bash
python scripts/reindex.py
```

### 刪除指定文件的索引

```bash
python scripts/reindex.py --delete 陸海空軍懲罰法.md
```

> **`--file` 行為**：先刪除該檔案的舊索引，再重新建立，等同於 upsert，不影響其他文件。


## 📖 相關文件
- **`AGENT.md`**：核心工程哲學與 Agent 設計說明。
- **`docs/DEVELOPER_GUIDE.md`**：詳細架構與模組指南。
- **`docs/DATABASE_CLEANUP.md`**：清空重建向量資料庫說明。