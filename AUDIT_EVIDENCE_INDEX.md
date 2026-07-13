# ISO 42001 稽核證據索引（Audit Evidence Index）

**編製日期**：2026-07-13　**系統版本**：v1.1.0 內網 10-service 復核基線
**用途**：稽核應對總導覽，列 ISO42001 RAG 主系統證據。

本版由龔修潁（RAG 相關後端）與張丘（強密碼、憑證、OpenWebUI）整理維護。

---

## 1. 系統範圍

| 範圍 | 證據 |
|---|---|
| RAG API 與檢索生成管線 | `RAG/rag_system/agent/`、`RAG/rag_system/services/`、`RAG/api.py` |
| 知識庫與向量索引 | `RAG/data/converted_md/`、PostgreSQL/pgvector |
| 稽核日誌與防竄改鏈 | `RAG/rag_system/core/audit_logger.py`、`RAG/docs/AUDIT_LOG_SCHEMA.md` |
| Prompt 與行為版本管理 | `RAG/docs/PROMPT_VERSIONS.md`、`RAG/rag_system/core/prompts.py` |
| 部署與內網入口 | `docker-compose.yaml`、`docker-compose.hardening.yml`、`nginx/nginx.conf`、`keycloak/import/iso42001-realm.json` |
| 維運管理與憑證卡認證 | `admin_console/`、`admin_console/tests/`、`docker-compose.yaml` `admin:8300` |
| 系統監測與 V&V | `monitoring_addon/`、`monitoring_addon/tests/`、`monitoring_addon/data/reports/` |
| 安全控制與人為監督 | `RAG/docs/SAFETY_CONTROLS.md`、`RAG/docs/governance/HUMAN_OVERSIGHT.md` |

## 2. 證據地圖

| ISO 要求 | 證據 | 狀態 |
|---|---|---|
| 組織脈絡與系統邊界 | `RAG/docs/SYSTEM_ARCHITECTURE_ANALYSIS.md` | 內網基線已更新 |
| 需求與適用範圍 | `RAG/docs/requirements_review_report.md` | 已建立 |
| 角色職責 | `RAG/docs/governance/RACI_MATRIX.md` | 待主管指派人員 |
| AI 風險評估 | `RAG/docs/governance/AI_RISK_ASSESSMENT.md` | 待評定等級與接受決定 |
| AI 影響評估 | `RAG/docs/governance/AI_IMPACT_ASSESSMENT.md` | 待審查負責人結論 |
| 模型卡 | `RAG/docs/governance/MODEL_CARD.md` | 草案待核定 |
| Prompt 版控 | `RAG/docs/PROMPT_VERSIONS.md` | 已建立 |
| 變更管理 | `RAG/CHANGELOG.md`、`RAG/data/versions/` | 乾淨基線保留 `.gitkeep`，正式快照可重新產生 |
| 稽核日誌 schema | `RAG/docs/AUDIT_LOG_SCHEMA.md` | 已建立 |
| 安全控制 | `RAG/docs/SAFETY_CONTROLS.md`、`RAG/tests/evaluation/test_prompt_security.py` | 已建立 |
| 引用溯源 | `RAG/rag_system/agent/nodes.py`、`RAG/tests/unit/test_verify_grounding.py` | classic graph 已建立 deterministic gate |
| 整合驗證 | `scripts/verify_project.sh` | Compose / shell / RAG / monitoring / admin 唯讀驗證入口 |
| 人為監督 | `RAG/docs/governance/HUMAN_OVERSIGHT.md` | 草案待核定 |
| 事件回應 | `RAG/docs/governance/INCIDENT_RESPONSE.md` | SLA/通報矩陣待單位核定 |

## 3. 稽核前待辦

1. 建立 `.env`，填入強密碼、Keycloak/OpenWebUI 設定與內網推論後端。
2. 啟動內網 stack 後，確認 `db`、`embed-proxy`、`rag-api`、`openwebui`、`keycloak`、`nginx`、`jupyter`、`code-server`、`monitoring`、`admin` 10 個服務。
3. 生產環境確認 classic graph 為預設（`REACT_MODE=false`），並保留 citation provenance gate 測試結果。
4. 確認 `ADMIN_CARD_SERIALS` 已設定；若使用 break-glass，需有啟用與關閉記錄。
5. 執行 `./scripts/verify_project.sh`，保留三套 pytest 與 Compose/hardening 檢查結果。
6. 重新建立法規索引，並保留 reindex 操作紀錄。
7. 由權責人員完成 `ETHICS_CHECKLIST.md`、`RACI_MATRIX.md`、`AI_RISK_ASSESSMENT.md`、`AI_IMPACT_ASSESSMENT.md` 簽核欄位。
8. 正式測試開始後，不再清空稽核日誌；需要證明完整性時，以 `AUDIT_LOG_SCHEMA.md` 所述雜湊鏈驗證。

## 4. 預期稽核問答

| 問題 | 回答位置 |
|---|---|
| 系統包含哪些服務？ | `README.md`、`RAG/docs/SYSTEM_ARCHITECTURE_ANALYSIS.md` |
| AI 回答如何追溯？ | `RAG/docs/AUDIT_LOG_SCHEMA.md`、`RAG/rag_system/core/audit_logger.py` |
| Prompt 變更如何控管？ | `RAG/docs/PROMPT_VERSIONS.md`、`RAG/CHANGELOG.md` |
| 使用者如何登入？ | OpenWebUI：`keycloak/import/iso42001-realm.json`；Admin：`admin_console/admincore/cardauth.py` |
| 回答引用如何回溯檢索證據？ | `RAG/rag_system/agent/nodes.py` citation provenance gate 與 `test_verify_grounding.py` |
| 管理入口如何降低暴露面？ | `RAG/docs/governance/DEPLOYMENT_HARDENING.md` |
| AI 出錯誰負責？ | `RAG/docs/governance/HUMAN_OVERSIGHT.md` |
