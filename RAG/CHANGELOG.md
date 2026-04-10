# 變更紀錄（Change Log）

*本紀錄由 `scripts/version_tracker.py` 自動產生，作為 ISO 42001 A.6/A.9 稽核證據。*
*每筆紀錄的「審核簽名」欄位由審核人員手動填寫。*

---

## [v1.1.0] - 2026-04-09
**操作者**：開發團隊  
**說明**：ISO 42001 合規實作完成（Phase 1-5）  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 新增檔案
- `rag_system/core/anomaly_detector.py` — 滑動視窗異常偵測模組
- `rag_system/core/auth.py` — API Key Bearer 認證
- `rag_system/core/rate_limiter.py` — 每 Key 速率限制
- `rag_system/core/input_sanitizer.py` — Prompt injection 防禦 / 輸入消毒
- `rag_system/core/output_filter.py` — 敏感資訊輸出過濾
- `rag_system/core/bias_evaluator.py` — 偏誤一致性評估
- `rag_system/core/retrieval_evaluator.py` — 檢索準確度指標（Hit Rate, Precision@K, MRR）
- `rag_system/core/answer_evaluator.py` — 回答正確性評估（關鍵字覆蓋、條文引用、結構檢查）
- `scripts/generate_monitoring_report.py` — A.6 監控報告產生器
- `scripts/run_vv_evaluation.py` — V&V 評估管線
- `scripts/version_tracker.py` — 無 Git 版本追蹤工具
- `tests/unit/test_anomaly_detector.py` — 異常偵測單元測試（12 cases）
- `tests/unit/test_auth.py` — 認證 / 速率限制測試（8 cases）
- `tests/evaluation/test_prompt_security.py` — Prompt injection 安全測試（34 cases）
- `tests/evaluation/test_bias_fairness.py` — 偏誤公平性測試（8 cases）
- `tests/evaluation/test_vv_pipeline.py` — V&V 管線測試（20 cases）
- `tests/evaluation/bias_test_dataset.json` — 偏誤測試資料集
- `docs/governance/ETHICS_CHECKLIST.md` — A.5 倫理審查清單

### 修改檔案
- `api.py` — 加入 Bearer 認證、CORS、速率限制、auth 事件日誌
- `rag_system/agent/graph.py` — 新增 security_block 節點與路由
- `rag_system/agent/nodes.py` — 整合 input_sanitizer（classify）、output_filter（generate）
- `rag_system/core/audit_logger.py` — 新增 log_security_alert()、log_auth_event()、擴充欄位
- `rag_system/core/config.py` — verify_ssl 預設 True、新增 VERIFY_SSL 環境變數
- `tests/evaluation/golden_dataset.json` — 從 3 筆擴充至 30 筆（含分類 / 難度）

### 刪除檔案
- `tests/unit/test_sources.py` — 參照不存在模組，已移除

---

## [v1.1.1] - 2026-04-09 06:36
**操作者**：開發團隊  
**說明**：新增版本追蹤工具與初始 CHANGELOG  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 新增檔案
- `CHANGELOG.md`

### 修改檔案
- `README.md`

---

## [v1.1.2] - 2026-04-09 07:14
**操作者**：開發團隊  
**說明**：auth.py 升級內網模式（IP 稽核追蹤）；README 全面更新  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 修改檔案
- `CHANGELOG.md`
- `README.md`
- `rag_system/core/auth.py`
- `tests/unit/test_auth.py`

---

## [v1.1.3] - 2026-04-09 07:21
**操作者**：開發團隊  
**說明**：README 全面擴充 Scripts 操作手冊  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 修改檔案
- `CHANGELOG.md`
- `README.md`

---

## [v1.1.4] - 2026-04-09 08:00
**操作者**：開發團隊  
**說明**：auth.py Fail-Closed（503）+ TRUSTED_PROXIES 防 IP 偽造；測試數更新至 113 cases  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 新增檔案
- `.ruff_cache/.gitignore`

### 修改檔案
- `CHANGELOG.md`
- `README.md`
- `api.py`
- `rag_system/core/anomaly_detector.py`
- `rag_system/core/auth.py`
- `scripts/run_vv_evaluation.py`
- `scripts/version_tracker.py`
- `tests/evaluation/test_vv_pipeline.py`
- `tests/unit/test_anomaly_detector.py`
- `tests/unit/test_auth.py`

---

## [v1.1.5] - 2026-04-09 08:31
**操作者**：開發團隊  
**說明**：input_sanitizer 擴充 SQL Injection/SSRF/CSRF 防禦；graph 新增 passthrough 節點；state 增加 threat_type/security_reason  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 修改檔案
- `README.md`
- `rag_system/agent/graph.py`
- `rag_system/agent/nodes.py`
- `rag_system/agent/state.py`
- `rag_system/core/input_sanitizer.py`

---

## [v1.1.6] - 2026-04-09 08:41
**操作者**：新增 LDAP Injection 偵測類別與對應測試案例  
**說明**：input_sanitizer 新增 LDAP Injection 防禦（七類攻擊）；test_prompt_security 擴充至 142 cases（新增 SQL/LDAP/SSRF/CSRF）  
**審核簽名**：＿＿＿＿＿＿＿＿  

### 修改檔案
- `README.md`
- `tests/evaluation/test_prompt_security.py`

---
