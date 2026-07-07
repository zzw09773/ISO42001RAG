# 部署強化指引（Deployment Hardening Guide）

> ISO 42001 A.8（安全）/ 條文 8（運作控制）證據。
> 對應 `SYSTEM_ARCHITECTURE_ANALYSIS.md` §7.1 的部署組態風險（R-INFRA-1~5）。
> 狀態：外部稽核準備整理版（2026-07-07）。修正方案已提供，**套用為系統管理者於維護窗口的決定**。

## 1. 風險與修正對照

| 風險 | 現況 | 修正方案 | 修正載體 | 套用狀態 |
|---|---|---|---|---|
| R-INFRA-5 | `.env.example` 缺失，部署 bootstrap 斷鏈 | 補完整範本（含安全強化註解） | `.env.example`（已建，純新增） | ✅ **已修正** |
| R-INFRA-3 | DB 預設帳密 + 宿主直連埠 | 埠綁 127.0.0.1 + `.env` 強制強密碼 | `docker-compose.hardening.yml` + `.env.example` | 🔧 已提供，待套用 |
| R-INFRA-1 | 多個服務仍有 host direct port；nginx 只保護 OpenWebUI 路徑 | direct port 綁 127.0.0.1，對外經 nginx TLS 或跳板 | `docker-compose.hardening.yml` | 🔧 已提供，待套用 |
| R-INFRA-2 | Jupyter 無認證 + sudo + rw 掛載（索引維運需求） | 埠綁 127.0.0.1 + 移除 sudo（+ 建議設 token） | `docker-compose.hardening.yml` | 🔧 已提供，待套用 |
| R-INFRA-4 | 無網段隔離 | 見 §3 進階建議（需正式變更） | 本指引 §3 | 📋 建議 |
| R-INFRA-6 | code-server 掛載整個專案與 Docker socket | direct port 綁 127.0.0.1 + 強密碼；必要時移除 Docker socket | `docker-compose.hardening.yml` + `.env.example` | 🔧 已提供，待套用 |

## 2. 套用步驟（需維護窗口，會 recreate 容器）

```bash
# 0. 前置：確認 .env 已填入強密碼（R-INFRA-3）
#    POSTGRES_PASSWORD 與 PGVECTOR_URL 內的密碼須一致且非預設值
grep -E 'POSTGRES_PASSWORD|WEBUI_SECRET_KEY|KEYCLOAK_ADMIN_PASSWORD|CODESERVER_PASSWORD' .env
# 確認非 postgres / your-secret-key-here / change-this-*，且 code-server/keycloak 為強密碼

# 1. 套用強化 override
docker compose -f docker-compose.yaml -f docker-compose.hardening.yml up -d

# 2. 驗證 direct port 綁定（除 nginx 8088/8443 外，應只見 127.0.0.1）
ss -tlnp | grep -E '15432|17100|8043|18088|18080|25678|18443|8088|8443'

# 3. 驗證功能未受影響
curl -fsS http://localhost:8043/health
```

**回退**：移除 `-f docker-compose.hardening.yml` 重新 `up -d` 即還原。

## 3. 進階建議（需正式變更，列為 roadmap）

- **R-INFRA-4 網段隔離**：在主 compose 為服務定義多個 `networks`（如 `frontend`/
  `backend`/`data`），使 jupyter/code-server 即使遭濫用也不能任意直連 db、
  embed-proxy。屬拓撲級變更，建議下一個維護版本納入。
- **R-INFRA-2 jupyter 強化**：jupyter 在 prod 運行（索引維運需求）。套用
  `docker-compose.hardening.yml` 將其埠綁 127.0.0.1（僅本機可達）並移除 sudo；
  建議另設 Jupyter token（改 `RAG/Dockerfile` 的 `--NotebookApp.token`）。
  若索引維運可改用 `docker exec rag-api python3 scripts/reindex.py`，則可進一步停用 jupyter。
- **R-INFRA-6 code-server 強化**：code-server 目前為內網維運便利性掛載整個專案與
  `/var/run/docker.sock`。正式部署建議移除 Docker socket 掛載，或只在跳板/VPN 中開放。
- **R-INFRA-1 nginx 反代 rag-api / keycloak / code-server**：若需要瀏覽器內網入口，
  在 `nginx.conf` 增加明確路徑或子網域反代，並保留 direct ports 只綁本機。對管理入口
  （Keycloak admin、code-server）應優先使用跳板或來源 IP allowlist。

## 4. 與稽核的關係

本指引將「已識別但未套用」的風險轉為「已提供修正方案、待維護窗口套用」的受控狀態——
這本身即 ISO 42001 條文 6.1（風險處置規劃）與條文 8（運作控制）的證據：風險不僅被
識別，且有具體、可回復、已驗證語法的處置方案。套用決定與時程由系統管理者依
`AI_RISK_ASSESSMENT.md` 的風險評定排序。
