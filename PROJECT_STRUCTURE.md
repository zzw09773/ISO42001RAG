# 專案結構導覽（Project Structure）

> ISO 42001 RAG 法律文件查詢系統 — 目錄地圖。供稽核員、維運人員、新進開發者
> 快速定位。最後更新：2026-07-14。
>
> 稽核證據總入口見 `AUDIT_EVIDENCE_INDEX.md`；架構說明見
> `RAG/docs/SYSTEM_ARCHITECTURE_ANALYSIS.md`。
> 本版由龔修潁（RAG 相關後端）與張丘（強密碼、憑證、OpenWebUI）整理維護。

## 頂層

| 目錄／檔案 | 角色 | 性質 |
|---|---|---|
| `RAG/` | RAG 主系統（FastAPI + LangGraph 檢索生成 + 稽核） | 原始碼＋證據 |
| `embed_proxy/` | OpenAI ↔ Triton gRPC embedding 轉譯層 | 原始碼 |
| `monitoring_addon/` | 健康、告警、漂移、V&V 與稽核儀表板 | 原始碼＋執行期資料 |
| `admin_console/` | 憑證卡登入的維運管理台；`data/rag-runtime.env` 僅同步非秘密 RAG 白名單設定 | 原始碼＋執行期設定 |
| `code-server/` | 瀏覽器 IDE image，預裝 Docker/container extension，掛載整個專案 | 設定 |
| `keycloak/` | Keycloak realm/client 匯入設定，供 OpenWebUI OIDC 與強密碼註冊 | 設定 |
| `nginx/` | HTTPS 反向代理設定與自簽憑證腳本 | 設定 |
| `docker-compose.yaml` | 10 服務編排：db / embed-proxy / rag-api / openwebui / keycloak / nginx / jupyter / code-server / monitoring / admin | 設定 |
| `docker-compose.hardening.yml` | 部署強化 override，含 `admin:8300` 的 loopback 限制 | 設定 |
| `.env.example` | 環境變數範本（佔位值＋安全強化註解） | 設定範本 |
| `deploy.sh` / `save_images.sh` / `make_update_package.sh` | 包含 admin image/code 的離線部署管線 | 腳本 |
| `backup_runtime.sh` / `restore_runtime.sh` / `verify_runtime_migration.sh` | 內網歷史冷備份、整庫還原、非內容式資料摘要驗證；保留目標新版 `.env` | 遷移腳本 |
| `scripts/verify_project.sh` | 不讀 `.env` 機密的 Compose、shell 與三套 pytest 本機驗證入口 | 驗證腳本 |
| `deploy_packages/` | 部署包（images tar＋code zip，**git 忽略**，可重建） | 建置產物 |
| `runtime_backups/` | 對話、帳號、向量、稽核與回退備份（**git 忽略**，須加密控管） | 機敏執行期資料 |
| `images/` | Docker tar 映像（**git 忽略**） | 建置產物 |
| `_dev_archive/` | 舊開發快照已清除；未來若重建仍不作為稽核證據 | 開發歸檔 |
| `AUDIT_EVIDENCE_INDEX.md` | 稽核證據總導覽 | 證據 |
| `PROJECT_STRUCTURE.md` | 本檔 | 文件 |

## RAG/ 主系統

| 路徑 | 內容 |
|---|---|
| `rag_system/agent/` | classic LangGraph 為預設；`nodes.py` 的 verify 含 citation provenance gate；`react_workflow.py` 僅在 `REACT_MODE=true` 時 opt-in |
| `rag_system/core/` | `audit_logger.py`（雜湊鏈日誌）、`auth.py`、`prompts.py`（單一 Prompt 基線版控）、`factory.py`、`input_sanitizer.py`、`output_filter.py`、`config.py` |
| `rag_system/services/` | `retrieval.py`（六階段混合檢索）、`ingestion.py`（Article-Aware Chunking）、`conversation_store.py` |
| `scripts/` | `reindex.py`（索引維運）、`version_tracker.py`（SHA-256 快照版控）、`run_vv_evaluation.py` |
| `data/converted_md/` | 知識庫原始法規 markdown（2 部法） |
| `data/audit_logs/` | 稽核日誌 JSONL（雜湊鏈，**git 忽略** — 含查詢內容） |
| `data/versions/` | 外部稽核準備已清空，只保留 `.gitkeep`；正式版控快照可重新產生 |
| `data/processed/` | 向量庫 docstore（執行期，忽略） |
| `tests/` | 評估與單元測試（含 runtime regression 與 citation provenance gate） |
| `docs/` | 合規文件（見下） |

### RAG/docs/ 合規文件

| 文件 | 對應 |
|---|---|
| `SYSTEM_ARCHITECTURE_ANALYSIS.md` | 系統性架構分析（74 主張＋12 限制） |
| `AUDIT_LOG_SCHEMA.md` | 稽核日誌格式（A.5.28/A.8.15） |
| `SAFETY_CONTROLS.md` | 安全縱深 9 守則（A.8） |
| `PROMPT_VERSIONS.md` | Prompt 基線版控史（A.4） |
| `INTRANET_DEPLOYMENT_RUNBOOK.html` | 部署 runbook |
| `INTRANET_HISTORY_MIGRATION.{md,html}` | 新服務驗證後載入既有內網歷史、驗證與回退 |
| `OPENWEBUI_USER_GUIDE.{md,html}` | OpenWebUI 一般使用者操作手冊 |
| `requirements_review_report.{md,html}` | 需求審查報告（v1.0.0） |
| `governance/` | 治理文件包（見下） |

### RAG/docs/governance/ 治理文件包

`MODEL_CARD` · `RACI_MATRIX` · `AI_RISK_ASSESSMENT` · `AI_IMPACT_ASSESSMENT` ·
`INCIDENT_RESPONSE` · `HUMAN_OVERSIGHT` · `ETHICS_CHECKLIST` · `DEPLOYMENT_HARDENING`

## git 衛生原則

- **忽略**：建置產物（deploy_packages、images、*.tar、*.zip）、執行期資料
  （audit_logs、reports、processed）、機密（.env、ssl 私鑰）、本機 IDE/工具狀態。
- `nginx/ssl/` 只追蹤 `.gitkeep`；`*.key` / `*.crt` / `*.csr` 由部署機產生或配發，不得進入 Git 或離線程式碼包。
- **追蹤**：原始碼、設定範本、文件、golden_dataset、必要 `.gitkeep`。
- 外部稽核準備以 Git 管理原始碼與文件；正式內網若不使用 Git，可重新啟用
  `version_tracker.py` 產生 SHA-256 快照（`RAG/data/versions/`）＋ CHANGELOG。

## 本機驗證入口

```bash
./scripts/verify_project.sh
```

腳本使用 `.env.example` 解析 base/hardening Compose，以 `bash -n` 檢查 shell，並執行部署契約、`RAG/tests`、`monitoring_addon/tests`、`admin_console/tests`；不讀取或印出 `.env` 內容。
