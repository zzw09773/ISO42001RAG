# ISO42001 實作系統 — 一鍵離線部署包

這是一個整合了 Retrieval-Augmented Generation (RAG) API、Open WebUI 視覺化前端介面、PostgreSQL 向量資料庫（pgvector）與 Jupyter Notebook 開發環境的完整系統。
本專案已設定為**完全支援內部網路的離線部署**，並解決了 Docker 環境下檔案權限（UID/GID）的問題。

---

## 🏗️ 系統服務架構

本系統透過單一 `docker-compose.yaml` 管理以下 **6 個核心服務**：

| 服務 | 說明 | Port |
|---|---|---|
| **pgvector** (`db`) | PostgreSQL + pgvector 向量資料庫 | `15432` |
| **embed-proxy** | OpenAI Embeddings API ↔ Triton gRPC 轉換層 | 內部 `8100` |
| **rag-api** | FastAPI RAG 服務（向量檢索 + LLM 回答） | `8000` |
| **openwebui** | 使用者聊天介面 | `8080` / `443` |
| **nginx** | HTTPS 反向代理 | `80`, `443` |
| **jupyter** | 開發 / 索引環境 | `25678` |

```
使用者瀏覽器
    │ HTTPS
    ▼
  Nginx (:443)
    ├── / → Open WebUI (:8080)
    └── API 請求 → RAG API (:8000)
                      ├── pgvector (:5432)   [向量搜尋]
                      ├── embed-proxy (:8100) [Embedding]
                      │       └── Triton gRPC (:9001) [GPU 推論]
                      └── LLM HTTP (:7000)   [語言生成]
```

---

## ⚙️ LLM 與 Embedding 服務設定

本系統不內建 LLM 或 Embedding 模型，需要外部的 **GPU 推論伺服器**（Triton）。

### 架構說明

```
┌─────────────────────────────────┐
│  GPU 推論伺服器（獨立機器）        │
│  ┌─────────────────────────┐    │
│  │  Triton Inference Server │    │
│  │  - LLM Model (HTTP)     │ :7000 (HTTP)
│  │  - nv-embed-v2 (gRPC)   │ :9001 (gRPC)
│  └─────────────────────────┘    │
└─────────────────────────────────┘
         ▲                ▲
         │                │
   直接 HTTP           gRPC（需轉換）
         │                │
   RAG API          embed-proxy
                  （OpenAI ↔ Triton 轉換層）
                          │
                     RAG API / Jupyter
```

### `.env` 參數說明

```ini
# ── 必填：GPU 推論伺服器 IP ──────────────────────────────────
LLM_HOST=172.16.120.35        # Triton 伺服器的 IP 位址

# ── LLM 服務（HTTP，OpenAI 相容） ────────────────────────────
LLM_PORT=7000                 # LLM HTTP port（Triton 的 OpenAI endpoint）
CHAT_MODEL_NAME=openai/gpt-oss-20b

# ── Embedding 服務（經由 embed-proxy 轉換 gRPC） ──────────────
# Triton 使用 gRPC，embed-proxy 負責轉換成 OpenAI /v1/embeddings 格式
# EMBED_PORT 是 embed-proxy 對外的 HTTP port（供 RAG API 使用，不是 Triton port）
EMBED_PORT=9000               # ← 此為 Triton gRPC port，embed-proxy 讀取此值
EMBED_MODEL_NAME=nvidia/nv-embed-v2

# ── API Key（Triton 端若有設定 JWT 驗證則填入） ───────────────
LLM_API_KEY=<Triton JWT Token>
EMBED_API_KEY=<Triton JWT Token>
```

> **embed-proxy 說明**：Triton 的 Embedding 服務使用 gRPC 協定，而 LangChain 只支援 OpenAI HTTP 格式。`embed-proxy` 容器作為中間層，將 `POST /v1/embeddings`（OpenAI 格式）轉換成 Triton gRPC 呼叫，RAG API 只需設定 `EMBED_API_BASE=http://embed-proxy:8100/v1` 即可。

### 搬遷到不同內網的修改步驟

只需修改 `.env` 中的一行：
```ini
LLM_HOST=<新的 GPU 伺服器 IP>
```
其他所有設定（port、API Key、模型名稱）若不變則不需修改。

---



在開始之前，請確保系統已安裝 **Docker** 與 **Docker Compose**。

1. **建立 `.env` 檔案**
   複製一份環境變數範本並修改：
   ```bash
   cp .env.example .env
   ```
   *⚠️ 您必須「手動修改」 `.env` 檔案中的設定：*
   - 打開 `.env` 檔案，找到 **`LLM_HOST=`** 這一行。
   - 將它修改為提供 LLM/Embedding API 的「GPU 推論伺服器」的內部 IP（例如 `LLM_HOST=192.168.1.100`）。
   - *（注意：至於本機的 IP 與目錄權限 UID/GID，`deploy.sh` 會全自動偵測處理，您不需手動填寫）*
   - **UID/GID (全自動)**：部署腳本會自動偵測您的使用者 ID 並覆寫此檔案。

2. **目錄結構與憑證**
   請確保已將 Nginx 的 SSL 自簽憑證放在以下路徑：
   - `nginx/ssl/cert.crt`
   - `nginx/ssl/cert.key`

---

## 🚀 部署流程（一鍵腳本）

系統設計為「先打包，後離線部署」的工作流：

### 階段一：有網路環境時打包 (Online Preparation)

在可以連線網際網路的機器上執行，此步驟會自動建置包含所有 Python pip 套件的 Image，並匯出為 `.tar` 檔。

```bash
chmod +x save_images.sh
./save_images.sh
```
執行完畢後，所有所需映像檔將打包至 `images/` 目錄下（共約 5.5GB）。

### 階段二：內網離線部署 (Offline Deployment)

將整個 `ISO42001Deploy/` 資料夾（包含 `images/` 目錄）複製到完全無網路的內網機器。
**只需執行一鍵部署腳本**：

```bash
chmod +x deploy.sh
./deploy.sh
```

**`deploy.sh` 的自動化行為包含：**
- **自動偵測 UID/GID**：自動抓取當前 Linux 使用者的 UID 與 GID 並更新至 `.env`，確保所有 Docker 產生的檔案您都有讀寫權限。
- **偵測 Docker Socket GID**：自動對應主機的 docker 群組，讓容器內也可以擁有正當權限。
- **離線影像載入**：自動從 `images/` 載入所有的 `.tar` 映像檔。
- **智慧判斷**：如果偵測到已經有現成的 RAG 與 Jupyter images，會自動套用 **「離線模式」**，不觸發會失敗的 build 動作。
- **等待健康檢查**：啟動後會等待（約 15-30 秒），直到所有核心服務被標記為 `healthy`。

---

## 🌐 服務存取與使用

部署完成後，可透過瀏覽器存取以下連結（請將 `<主機IP>` 替換為實際的伺服器 IP）：

- **聊天介面 (Open WebUI)**: `https://<主機IP>/` 
- **RAG API 狀態**: `http://<主機IP>:8000/health`
- **開發環境 (Jupyter)**: `http://<主機IP>:25678/`

*附註：由於 Nginx 使用的是本地自簽憑證，在首次存取 HTTPS (Open WebUI) 時，瀏覽器會跳出「您的連線不是私人連線」的警告。請點擊「進階」並選擇「繼續前往」即可。*

---

## 🧹 系統清理

如需停止並移除所有容器與網路設定，請執行：
```bash
docker compose down
```

如需移除包含資料庫、Open WebUI 對話紀錄在內的所有長效資料（**請謹慎執行**）：
```bash
docker compose down -v
```
