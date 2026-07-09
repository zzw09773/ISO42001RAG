# 專案結構導覽（Project Structure）

> ISO 42001 RAG 法律文件查詢系統 — 目錄地圖。供稽核員、維運人員、新進開發者
> 快速定位。最後更新：2026-07-02。
>
> 稽核證據總入口見 `AUDIT_EVIDENCE_INDEX.md`；架構說明見
> `RAG/docs/SYSTEM_ARCHITECTURE_ANALYSIS.md`。
> 本版由龔修潁（RAG 相關後端）與張丘（強密碼、憑證、OpenWebUI）整理維護。

## 頂層

| 目錄／檔案 | 角色 | 性質 |
|---|---|---|
| `RAG/` | RAG 主系統（FastAPI + LangGraph 檢索生成 + 稽核） | 原始碼＋證據 |
| `embed_proxy/` | OpenAI ↔ Triton gRPC embedding 轉譯層 | 原始碼 |
| `code-server/` | 瀏覽器 IDE image，預裝 Docker/container extension，掛載整個專案 | 設定 |
| `keycloak/` | Keycloak realm/client 匯入設定，供 OpenWebUI OIDC 與強密碼註冊 | 設定 |
| `nginx/` | HTTPS 反向代理設定與自簽憑證腳本 | 設定 |
| `docker-compose.yaml` | 外部稽核準備服務編排：rag-api / embed-proxy / jupyter / openwebui / keycloak / code-server / nginx，加上 db 依賴 | 設定 |
| `docker-compose.hardening.yml` | 部署強化 override（R-INFRA 修正，可一鍵套用） | 設定 |
| `.env.example` | 環境變數範本（佔位值＋安全強化註解） | 設定範本 |
| `deploy.sh` / `save_images.sh` | 離線部署管線 | 腳本 |
| `deploy_packages/` | 部署包（images tar＋code zip，**git 忽略**，可重建） | 建置產物 |
| `images/` | Docker tar 映像（**git 忽略**） | 建置產物 |
| `_dev_archive/` | 舊開發快照已清除；未來若重建仍不作為稽核證據 | 開發歸檔 |
| `AUDIT_EVIDENCE_INDEX.md` | 稽核證據總導覽 | 證據 |
| `PROJECT_STRUCTURE.md` | 本檔 | 文件 |

## RAG/ 主系統

| 路徑 | 內容 |
|---|---|
| `rag_system/agent/` | LangGraph 工作流：`graph.py`（圖拓撲）、`nodes.py`（classify/retrieve/generate/verify）、`state.py`、`react_workflow.py`（ReAct 原型，預設關閉） |
| `rag_system/core/` | `audit_logger.py`（雜湊鏈日誌）、`auth.py`、`prompts.py`（單一 Prompt 基線版控）、`factory.py`、`input_sanitizer.py`、`output_filter.py`、`config.py` |
| `rag_system/services/` | `retrieval.py`（六階段混合檢索）、`ingestion.py`（Article-Aware Chunking）、`conversation_store.py` |
| `scripts/` | `reindex.py`（索引維運）、`version_tracker.py`（SHA-256 快照版控）、`run_vv_evaluation.py` |
| `data/converted_md/` | 知識庫原始法規 markdown（2 部法） |
| `data/audit_logs/` | 稽核日誌 JSONL（雜湊鏈，**git 忽略** — 含查詢內容） |
| `data/versions/` | 外部稽核準備已清空，只保留 `.gitkeep`；正式版控快照可重新產生 |
| `data/processed/` | 向量庫 docstore（執行期，忽略） |
| `tests/` | 評估與單元測試（V&V、prompt 安全、偏誤公平、認證、異常偵測） |
| `docs/` | 合規文件（見下） |

### RAG/docs/ 合規文件

| 文件 | 對應 |
|---|---|
| `SYSTEM_ARCHITECTURE_ANALYSIS.md` | 系統性架構分析（74 主張＋12 限制） |
| `AUDIT_LOG_SCHEMA.md` | 稽核日誌格式（A.5.28/A.8.15） |
| `SAFETY_CONTROLS.md` | 安全縱深 9 守則（A.8） |
| `PROMPT_VERSIONS.md` | Prompt 基線版控史（A.4） |
| `INTRANET_DEPLOYMENT_RUNBOOK.html` | 部署 runbook |
| `requirements_review_report.{md,html}` | 需求審查報告（v1.0.0） |
| `governance/` | 治理文件包（見下） |

### RAG/docs/governance/ 治理文件包

`MODEL_CARD` · `RACI_MATRIX` · `AI_RISK_ASSESSMENT` · `AI_IMPACT_ASSESSMENT` ·
`INCIDENT_RESPONSE` · `HUMAN_OVERSIGHT` · `ETHICS_CHECKLIST` · `DEPLOYMENT_HARDENING`

## git 衛生原則

- **忽略**：建置產物（deploy_packages、images、*.tar、*.zip）、執行期資料
  （audit_logs、reports、processed）、機密（.env、ssl 私鑰）、本機 IDE/工具狀態。
- **追蹤**：原始碼、設定範本、文件、golden_dataset、必要 `.gitkeep`。
- 外部稽核準備以 Git 管理原始碼與文件；正式內網若不使用 Git，可重新啟用
  `version_tracker.py` 產生 SHA-256 快照（`RAG/data/versions/`）＋ CHANGELOG。

## 維運建議（未執行，待決定）

- 外部稽核準備目前已刪除舊部署包、Docker tar、audit log、版本 snapshot、快取、舊 `.env` 與本機工具狀態。
