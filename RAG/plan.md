# ISO 42001 合規實作計畫 — RAG 專案

## 實作狀態：✅ Phase 1–5 程式碼已完成

**驗證日期**：2026-04-09  
**測試結果**：102 tests ALL PASSED（於 ISO42001_jupyter 容器內執行）  
**報告產出**：monitoring_2026-04-09.md / vv_report_2026-04-09.md 均正常產生

---

## 問題陳述

RAG 專案需要符合 ISO 42001 標準規範，經分析後原始合規程度約 30-40%（基礎級）。以下五大領域已補強：

1. **模型監控與異常檢測** — ✅ 已實作異常偵測模組 + 監控報告產生器
2. **模型授權與存取控制** — ✅ API Key 認證 + 速率限制 + CORS + SSL 預設啟用
3. **提示詞安全（Prompt Injection 防禦）** — ✅ 輸入消毒 + 輸出過濾 + 34 個安全測試案例
4. **偏誤與倫理驗證** — ✅ 偏誤評估框架 + 倫理審查清單
5. **RAG 檢索準確度與 V&V** — golden dataset 僅 3 筆且格式不足計算指標、無自動化評估管線

---

## 現況分析摘要（程式碼審查後更新）

### 已有基礎 ✅
- `rag_system/core/audit_logger.py`：JSON Lines 結構化日誌，每日輪替，記錄 query/rejection/upload/reindex 四種事件
- `rag_system/core/prompts.py`：集中式系統提示詞，有範圍限制（法律領域）與關鍵字分類
- `rag_system/agent/nodes.py verify_node`：基本引用格式驗證 + 重試機制（MAX_RETRIES=2）
- `rag_system/services/retrieval.py`：混合檢索（BM25 + Vector + LLM reranking）+ 來源多樣性
- `docs/governance/`：GOVERNANCE.md、ISO_42001_GUIDE.md 等治理文件
- `tests/evaluation/golden_dataset.json`：3 筆評估樣本（路徑為 tests/evaluation/，非根目錄）

### 關鍵缺口 ❌（程式碼審查後確認）
- **所有** API 端點（chat, upload, delete, reindex, list）均**無任何身份驗證**
- `verify_ssl: bool = False`：欄位存在但 `from_env()` 完全未讀取此環境變數，永遠為 False
- 無 CORS middleware（api.py 完全未設定）
- 無 prompt injection 防禦或輸入驗證
- `tests/unit/test_sources.py` 引用不存在的模組（`rag_system.node`）與函數，整個測試套件無法執行
- `tests/evaluation/golden_dataset.json` 缺少 `expected_answer`、`expected_docs` 欄位，IR 指標無法計算
- 無偏誤測試框架
- 無自動化 V&V 管線或檢索準確度指標（NDCG, MRR, Precision@K）
- 無異常偵測閾值或告警系統

---

## 前置條件（開工前必須處理）

### Pre-0. 修正失效測試 (`tests/unit/test_sources.py`)
- 現況：引用不存在的 `rag_system.node` 模組與三個不存在的函數
- 行動：刪除此檔案（測試的函數已不存在於 codebase）
- 理由：保留失效測試會讓 CI 永遠紅燈，阻礙後續所有測試工作

---

## 實作計畫

### Phase 3：提示詞安全與 Prompt Injection 防禦（最高優先）

**目標**：防止惡意指令複寫系統規則、防止洩漏連接字串/伺服器路徑，滿足 ISO 42001 A.8

#### 3-1. 輸入消毒模組 (`rag_system/core/input_sanitizer.py` — 新增)
- 輸入長度限制（可設定最大字元數，預設 2000）
- 危險模式偵測與阻擋：
  - Prompt injection：`ignore previous instructions`、`system:`、`<|im_start|>`、`</s>` 等
  - 系統資訊探測：要求揭露 system prompt、連接字串、API key、伺服器路徑
  - 角色切換攻擊：`你現在是...`、`act as...`、`pretend you are...`、`DAN`
- 偵測到惡意輸入時：
  - 記錄完整事件到 audit log（event_type: `security_alert`）
  - 回傳標準化拒絕訊息
  - 不將惡意內容傳給 LLM

#### 3-2. 輸出過濾模組 (`rag_system/core/output_filter.py` — 新增)
- 檢查 LLM 回應是否洩漏敏感資訊：
  - 連接字串模式（`postgresql://`、`mongodb://`、`redis://`）
  - 伺服器路徑（`/home/aia/`、`/var/`、`/etc/`、`C:\`）
  - API Key / Token 模式（長隨機字串、`Bearer`）
  - 系統提示詞段落（直接引用 AGENT_SYSTEM_PROMPT 內容）
- 偵測到洩漏時進行遮蔽並記錄 audit log

#### 3-3. Prompt Injection 測試套件 (`tests/evaluation/test_prompt_security.py` — 新增)
- 測試案例涵蓋：
  - 直接 prompt injection（要求忽略系統指令）
  - 系統資訊探測（要求吐出連接字串、路徑）
  - 角色切換攻擊
  - Unicode/編碼繞過
  - 多語言攻擊（中英混合）
- 每個測試案例驗證 input_sanitizer 正確偵測並拒絕

#### 3-4. 整合到處理流程 (`rag_system/agent/nodes.py` 修改)
- 在 `classify_node` 之前的輸入通過 `input_sanitizer`
- 在 `generate_node` 之後通過 `output_filter`
- 所有安全事件記錄到 audit log

**產出證據**：Prompt injection 測試報告、安全事件 audit log

---

### Phase 2：模型授權與存取控制

**目標**：實作 API 層身份驗證與授權控制，滿足 ISO 42001 A.3/A.9

#### 2-1. API 認證中介層 (`rag_system/core/auth.py` — 新增)
- 實作 API Key 驗證機制：
  - 從 `Authorization: Bearer <key>` header 驗證
  - 支援多組 API Key（從環境變數 `API_KEYS` 逗號分隔載入）
  - 驗證失敗回傳 401 並記錄 audit log
- 豁免路由：`/health`（監控用）

#### 2-2. 速率限制 (`rag_system/core/rate_limiter.py` — 新增)
- 基於 sliding window 的速率限制（in-memory）：
  - 每個 API Key 的每分鐘請求數上限（預設 60）
  - 超限回傳 429 並記錄 audit log

#### 2-3. API 端點整合 (`api.py` 修改)
- 加入 CORS middleware（CORSMiddleware）
- 保護**所有**端點：chat, upload, upload/batch, delete, reindex, list
- 記錄認證成功/失敗事件到 audit log

#### 2-4. 安全設定修正 (`rag_system/core/config.py` 修改)
- `verify_ssl` 預設值改為 `True`
- `from_env()` 加入讀取 `VERIFY_SSL` 環境變數（目前完全缺失）
- 新增 `API_KEYS`、`RATE_LIMIT_PER_MINUTE`、`ALLOWED_ORIGINS` 環境變數

#### 2-5. 授權測試 (`tests/unit/test_auth.py` — 新增)
- 測試有效/無效 API Key 的 401 回應
- 測試速率限制的 429 回應
- 測試 /health 路由豁免

**產出證據**：API 認證日誌、速率限制觸發紀錄

---

### Phase 1：模型監控與異常檢測（證據產出）

**目標**：建立可量化的監控機制，產出 ISO 42001 A.6 Lifecycle Monitoring 所需證據

#### 1-1. 監控指標增強 (`rag_system/core/audit_logger.py`)
- 在現有 audit log 的 `log_query` 中增加以下欄位：
  - `retrieval_doc_count`：實際檢索到的文件數
  - `citation_count`：回答中引用條文數量（`第X條` 出現次數）
  - `retry_count`：重試次數
  - `anomaly_flags`：異常標記列表
- 新增 `log_security_alert` 方法（給 Phase 3 使用）
- 新增 `log_auth_event` 方法（給 Phase 2 使用）

#### 1-2. 異常檢測模組 (`rag_system/core/anomaly_detector.py` — 新增)
- 基於滑動視窗的統計異常偵測：
  - 回應時間異常（超過近期 p95 的 2 倍）
  - 拒絕率異常（短時間內拒絕率突增 > 50%）
  - 安全事件頻率異常（同一 IP/session 短時間多次觸發）
  - 連續重試異常（同一 session retry_count ≥ 2）
- 異常事件寫入 audit log 並填入 `anomaly_flags`

#### 1-3. 監控報告產生器 (`scripts/generate_monitoring_report.py` — 新增)
- 讀取 `data/audit_logs/*.jsonl`，產生：
  - 每日摘要統計（查詢數、拒絕率、平均延遲、P95 延遲、安全事件數）
  - 異常事件清單
- 輸出 JSON + Markdown 報告，作為稽核證據

#### 1-4. 監控測試 (`tests/unit/test_anomaly_detector.py` — 新增)
- 測試各種異常情境的偵測正確性
- 測試報告產生器的輸出格式

**產出證據**：監控報告（Markdown/JSON）、異常偵測紀錄

---

### Phase 5：RAG 檢索準確度與 V&V 測試

**目標**：建立完整的驗證與確認（V&V）框架，滿足 ISO 42001 A.6

#### 5-1. 擴充 Golden Dataset (`tests/evaluation/golden_dataset.json` 重新設計)
- **格式修正**（現有格式缺少 IR 指標所需欄位）：
  ```json
  {
    "id": "eval_001",
    "question": "...",
    "expected_answer": "...",
    "expected_docs": ["source_filename.md"],
    "expected_keywords": ["條文關鍵字"],
    "expected_articles": ["法規名稱"],
    "difficulty": "easy|medium|hard",
    "category": "single_article|cross_reference|ambiguous|out_of_scope"
  }
  ```
- 從 3 筆擴充至 30+ 筆，涵蓋各類查詢型態

#### 5-2. 檢索準確度評估 (`rag_system/core/retrieval_evaluator.py` — 新增)
- 實作標準 IR 指標：
  - Precision@K（前 K 筆檢索結果的精確度）
  - MRR（Mean Reciprocal Rank）
  - Hit Rate（至少命中一個相關文件的比率）
- 以 `expected_docs` 為 ground truth

#### 5-3. 回答正確性評估 (`rag_system/core/answer_evaluator.py` — 新增)
- 關鍵字覆蓋率：回答是否包含 `expected_keywords`
- 條文引用正確性：引用的條文是否與 `expected_articles` 一致
- 結構完整性：回答是否包含所有必要段落

#### 5-4. 自動化 V&V 管線 (`scripts/run_vv_evaluation.py` — 新增)
- 執行完整 golden dataset 評估（不需真實 LLM，僅評估現有 retrieval）
- 產生結構化評估報告（JSON + Markdown）
- 設定通過閾值（Precision@5 > 0.6, Hit Rate > 0.7）

#### 5-5. V&V 測試 (`tests/evaluation/test_vv_pipeline.py` — 新增)
- 測試評估指標計算正確性（單元測試，不需實際 LLM 呼叫）
- 測試報告產生格式

**產出證據**：V&V 評估報告、Golden dataset（含評估維度）

---

### Phase 4：偏誤與倫理驗證（最低優先）

**目標**：建立偏誤偵測機制，滿足 ISO 42001 A.5/A.7

#### 4-1. 偏誤測試資料集 (`tests/evaluation/bias_test_dataset.json` — 新增)
- 設計不同面向的對照組測試案例（性別、族群、社經地位、身心障礙）

#### 4-2. 偏誤評估框架 (`rag_system/core/bias_evaluator.py` — 新增)
- 一致性檢測：對照組問題的回答關鍵字覆蓋率應相近

#### 4-3. 偏誤測試執行器 (`tests/evaluation/test_bias_fairness.py` — 新增)
- 自動化執行偏誤測試資料集，產生報告

#### 4-4. 倫理審查清單 (`docs/governance/ETHICS_CHECKLIST.md` — 新增)
- 基於 ISO 42001 A.5 的 AI 影響評估模板

**產出證據**：偏誤測試報告、倫理審查清單

---

## 實作順序

```
Pre-0: 修正失效測試 (tests/unit/test_sources.py 刪除)
  │
  ├─ Phase 3 (安全) — 最高優先，防止資料洩漏
  ├─ Phase 2 (授權) — 次高優先，所有端點無驗證
  ├─ Phase 1 (監控) — 為 Phase 4/5 提供基礎設施
  ├─ Phase 5 (V&V)  — 稽核證據價值高
  └─ Phase 4 (偏誤) — 最低優先
```

---

## 檔案變更清單

### 刪除檔案
| 檔案 | 原因 |
|------|------|
| `tests/unit/test_sources.py` | 引用不存在模組，死測試 |

### 新增檔案
| 檔案 | Phase | 用途 |
|------|-------|------|
| `rag_system/core/input_sanitizer.py` | 3 | 輸入消毒 |
| `rag_system/core/output_filter.py` | 3 | 輸出過濾 |
| `rag_system/core/auth.py` | 2 | API 認證中介層 |
| `rag_system/core/rate_limiter.py` | 2 | 速率限制 |
| `rag_system/core/anomaly_detector.py` | 1 | 異常偵測模組 |
| `rag_system/core/retrieval_evaluator.py` | 5 | 檢索準確度評估 |
| `rag_system/core/answer_evaluator.py` | 5 | 回答正確性評估 |
| `rag_system/core/bias_evaluator.py` | 4 | 偏誤評估 |
| `scripts/generate_monitoring_report.py` | 1 | 監控報告產生器 |
| `scripts/run_vv_evaluation.py` | 5 | V&V 管線執行器 |
| `tests/unit/test_auth.py` | 2 | 授權測試 |
| `tests/unit/test_anomaly_detector.py` | 1 | 異常偵測測試 |
| `tests/evaluation/test_prompt_security.py` | 3 | Prompt injection 測試 |
| `tests/evaluation/test_bias_fairness.py` | 4 | 偏誤測試 |
| `tests/evaluation/bias_test_dataset.json` | 4 | 偏誤測試資料集 |
| `tests/evaluation/test_vv_pipeline.py` | 5 | V&V 測試 |
| `docs/governance/ETHICS_CHECKLIST.md` | 4 | 倫理審查清單 |

### 修改檔案
| 檔案 | Phase | 變更內容 |
|------|-------|----------|
| `rag_system/core/audit_logger.py` | 1 | 新增 log_security_alert、log_auth_event、增加監控欄位 |
| `rag_system/core/config.py` | 2 | 修正 verify_ssl env 讀取、新增 AUTH/RATE_LIMIT/CORS 設定 |
| `api.py` | 2 | 加入 CORSMiddleware、認證依賴、保護所有端點 |
| `rag_system/agent/nodes.py` | 3 | 加入 input_sanitizer、output_filter |
| `tests/evaluation/golden_dataset.json` | 5 | 重新設計格式並擴充至 30+ 筆 |
| `requirements.txt` | all | 新增必要依賴（slowapi 或 pure-Python rate limiter） |

---

## 稽核證據對照表（ISO 42001 Annex A）

| 控制項 | 證據來源 | Phase |
|--------|----------|-------|
| A.3 內部組織 | API 認證日誌、RBAC 設定 | 2 |
| A.5 影響評估 | 偏誤測試報告、倫理審查清單 | 4 |
| A.6 生命週期 | 監控報告、V&V 評估報告、異常偵測紀錄 | 1, 5 |
| A.7 資料 | 來源多樣性分析、偏誤測試結果 | 4, 5 |
| A.8 資訊透明 | Prompt injection 測試報告、輸入/輸出安全紀錄 | 3 |
| A.9 使用 | 授權控制紀錄、速率限制紀錄、範圍檢查日誌 | 2, 3 |

---

## 版本控制策略（無 Git / 無 GitLab 內網環境）

ISO 42001 A.6（AI 系統生命週期）和 A.9（使用紀錄）要求對 AI 系統的變更進行版控與追蹤。
內網環境無 Git 也無 GitLab，但有 Docker + Python + 基本 Linux 指令。

### 解決方案：Python 自動化版本追蹤系統

提供一支 `scripts/version_tracker.py` 腳本，自動化以下功能：

1. **快照（snapshot）**：計算所有原始碼檔案的 SHA-256 雜湊，存入 `data/versions/` 作為基準線
2. **差異偵測（diff）**：比對當前檔案與上次快照，列出新增/修改/刪除的檔案
3. **變更記錄（changelog）**：將變更事件追加寫入 `CHANGELOG.md`，含時間戳、操作者、變更說明
4. **完整性驗證（verify）**：稽核時可隨時驗證原始碼是否與紀錄版本一致

### 同時維護 `CHANGELOG.md`

手動 + 腳本輔助並行：
- 每次重要變更後執行 `python3 scripts/version_tracker.py snapshot --message "說明"`
- 腳本自動偵測變更檔案，自動追加到 CHANGELOG.md
- 重大版本由人員手動補充「審核簽名」欄位

### ISO 42001 對版控的最低要求

| 要求 | 滿足方式 | 證據來源 |
|------|----------|----------|
| 變更可追溯 | ✅ SHA-256 快照比對 | `data/versions/snapshot_*.json` |
| 變更有說明 | ✅ snapshot --message | CHANGELOG.md |
| 版本可識別 | ✅ 快照時間戳 + 版本號 | 快照檔案名稱 |
| 可回滾 | ✅ tar 備份 + 快照比對 | `data/versions/backup_*.tar.gz` |
| 變更審核 | ✅ CHANGELOG 審核簽名欄 | CHANGELOG.md |
| 完整性驗證 | ✅ verify 指令 | 比對雜湊報告 |
