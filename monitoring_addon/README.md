# Monitoring Addon — ISO 42001 漂移監測與儀表板

> **設計約束**：本子專案**完全獨立於 `RAG/` 主系統**，採「讀寫分離」原則。
> 只從 `../RAG/data/audit_logs/` 與 `../RAG/tests/evaluation/golden_dataset.json` **讀取**資料，
> **絕不寫入** `RAG/` 任何位置。所有輸出都落在本目錄的 `data/reports/`。

---

## 🎯 業務目標

| 目標 | 門檻 | 衡量方式 |
|---|---|---|
| **RAG 準確率** | `Hit Rate ≥ 0.90` | `scripts/run_extended_vv.py` 對黃金資料集評估，Hit Rate 是**唯一 gating 指標**（其他 IR 指標為 informational） |

設定點：`monitoring/config.py` 的 `BUSINESS_GOAL_HIT_RATE`。
儀表板會在最上方以「業務目標達標卡」顯示三態：
- ✅ **目標達成**：Hit Rate ≥ 0.90
- ❌ **目標未達**：Hit Rate < 0.90
- ⚠️ **尚未驗證**：V&V 未跑過 / 黃金資料集無 `expected_docs`（無 ground truth）

---

## 為什麼是 Addon

`RAG/` 主系統已通過 ISO 42001 v1.0.0 需求審查，後續若要新增功能不應動到主系統的 `rag_system/` 套件、`scripts/`、`api.py`。本 addon 是**側掛式**的監測層，與主系統的關係是：

```
                   ┌─ 主系統（凍結） ──────────────────┐
                   │  RAG/api.py        ← /v1/chat...   │
                   │  RAG/rag_system/   ← LangGraph     │
                   │  RAG/scripts/      ← reindex/V&V   │
                   │  RAG/data/audit_logs/*.jsonl  ─┐    │
                   │  RAG/tests/evaluation/        ─┤    │
                   │      golden_dataset.json       │    │
                   └────────────────────────────────│────┘
                                                    │ 只讀
                   ┌─ Monitoring Addon（本目錄）────▼────┐
                   │  monitoring/        ← 核心邏輯      │
                   │  scripts/           ← CLI 入口      │
                   │  service/           ← 即時儀表板    │
                   │  data/reports/      ← 漂移/儀表板輸出│
                   └────────────────────────────────────┘
```

---

## 功能

| 功能 | 模組 | 輸出 |
|---|---|---|
| **IR 指標補齊**（Recall@K, F1@K） | `monitoring/ir_metrics.py` | 由 `scripts/run_extended_vv.py` 產生 `data/reports/extended_vv_*.{json,md}` |
| **漂移監測**（Performance / Data / Embedding） | `monitoring/drift_detector.py` | `scripts/run_drift_detection.py` → `data/reports/drift_*.{json,md}` |
| **靜態儀表板**（離線 HTML，內嵌 SVG） | `scripts/build_dashboard.py` + `templates/dashboard.html` | `data/reports/dashboard_*.html` |
| **動態儀表板**（FastAPI 即時） | `service/app.py`（port `8200`） | `GET /dashboard`、`GET /v1/dashboard/data` |

---

## 快速開始

### 1. 依賴

```bash
pip install -r requirements.txt
```

`numpy` 是 optional：若已安裝，embedding drift 用 PCA + 真正 embedding；
若未安裝，自動退化為字元 n-gram 統計。

### 2. 跑漂移監測

```bash
# 從主系統 audit logs 讀資料，以 golden dataset 為基線
python3 scripts/run_drift_detection.py \
  --audit-dir ../RAG/data/audit_logs \
  --golden    ../RAG/tests/evaluation/golden_dataset.json \
  --baseline  ../RAG/data/reports/vv_report_2026-04-09.json \
  --window-days 7
```

輸出：

- `data/reports/drift_YYYY-MM-DD.json`（機器可讀）
- `data/reports/drift_YYYY-MM-DD.md`（稽核呈交用）

### 3. 跑擴充 V&V（含 Recall@K / F1@K）

```bash
python3 scripts/run_extended_vv.py \
  --golden ../RAG/tests/evaluation/golden_dataset.json
```

### 4. 產生靜態儀表板

```bash
python3 scripts/build_dashboard.py \
  --audit-dir ../RAG/data/audit_logs \
  --reports-dir ./data/reports
```

輸出：`data/reports/dashboard_YYYY-MM-DD.html`（雙擊可開、可列印、可附稽核包）。

### 5. 啟動即時儀表板服務

```bash
uvicorn service.app:app --host 0.0.0.0 --port 8200
```

瀏覽器開 <http://localhost:8200/dashboard>。

> **Port 選擇**：8200 是刻意避開 RAG API (8000)、embed-proxy (8100) 與常見 VSCode forwarded ports (8000/8100) 的位置。若 8200 也被佔用，用 `MONITORING_PORT=8201 uvicorn ...` 自行調整。

---

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `RAG_DATA_DIR` | `../RAG/data` | 主系統 data 目錄 |
| `GOLDEN_DATASET` | `../RAG/tests/evaluation/golden_dataset.json` | 基線資料集路徑 |
| `EMBED_API_BASE` | `http://localhost:8100/v1` | 主系統的 embed-proxy（給 embedding drift 用；別跟下方 MONITORING_PORT 搞混） |
| `EMBED_API_KEY` | _empty_ | embed-proxy token |
| `EMBED_MODEL_NAME` | `nvidia/nv-embed-v2` | embedding 模型名稱 |
| `MONITORING_PORT` | `8200` | 動態儀表板服務 port（避開 RAG API 8000 與 embed-proxy 8100） |

---

## 漂移嚴重度門檻

`monitoring/thresholds.py` 中的 `classify_drift_severity()` **是業務風險容忍度的具現**，
門檻數值由稽核負責人填寫，而非工程預設值。詳見該檔案內部 docstring。

---

## ISO 42001 合規對應

| 控制項 | 本 addon 證據 |
|---|---|
| A.6.2.4 Lifecycle Monitoring | `data/reports/drift_*.md` 漂移趨勢；`data/reports/dashboard_*.html` 視覺化證據 |
| A.6.2.8 V&V | `data/reports/extended_vv_*.md`（含 Recall@K / F1@K） |
| A.9.2 使用紀錄 | 透過讀取 `RAG/data/audit_logs/` 二次分析，原始紀錄不被修改 |

---

## 測試

```bash
python3 -m pytest tests/ -v
```

---

## 與主系統的解耦保證

- 本 addon **不 import** `rag_system.*` 任何模組。
- 本 addon **不寫入** `RAG/` 任何子目錄。
- 本 addon 的 FastAPI 服務使用獨立 port（`8200`），不與 RAG API（`8000`）或 embed-proxy（`8100`）衝突。
- Embedding drift 透過 HTTP 呼叫 embed-proxy，不直接 import embedding client。
