# 部署強化指引（Deployment Hardening Guide）

> ISO 42001 A.8（安全）與條文 8（運作控制）證據。
> 狀態：2026-07-13 內網 10-service stack 復核版。

## 1. 邊界與保護目標

`docker-compose.yaml` 維持開發與維運可用性；正式交付應合併 `docker-compose.hardening.yml`。強化 override 將以下 direct ports 綁定到 `127.0.0.1`：

- DB `15432`、embed-proxy `17100`、RAG API `8043`；
- OpenWebUI `18088`、Keycloak `18080`、Jupyter `25678`；
- code-server `18443`、monitoring `8200`、admin `8300`。

nginx `8088/8443` 是預期的網路入口。`admin` 不經 nginx，因具有 `.env` 維護、RAG 重啟與受限 Docker exec 能力，必須同時使用 loopback、跳板/VPN 或等效的來源限制。

## 2. 強化對照

| 風險 | Base Compose | Hardening / 作業控制 |
|---|---|---|
| 多個明文 direct ports | 可對宿主網路發布 | 所有非 nginx direct ports（含 `admin:8300`）綁定 loopback |
| DB 繞過應用層 | `15432` 直連 | loopback + `.env` 強隨機密碼 |
| Jupyter 開發能力 | 可寫原始碼，開發 image 權限高 | loopback；正式環境建議關閉或設 token、移除 sudo |
| code-server + Docker socket | 影響整個專案與 Docker host | loopback + 強密碼；必要時移除 socket |
| admin + Docker socket + `.env` | 可改設定、重啟與執行白名單作業 | 憑證卡白名單 + loopback；break-glass 帳密預設關閉 |
| RAG 設定重載 | 若掛整份 `.env` 會跨服務暴露管理密碼 | 只同步 13 個非秘密鍵至 `admin_console/data/rag-runtime.env`，rag-api 唯讀掛載 |
| TLS 私鑰洩漏 | 將私鑰帶入 Git/包裝會失去機密性 | 工作樹刪除舊憑證並由部署機生成或配發；歷史 key 視為已洩漏且必須輪替 |

## 3. 部署前置

1. 將 `.env.example` 複製為 `.env`，權限設為 `0600`，並透過授權的機密流程填入強隨機值。不要把密碼值輸出到終端紀錄。
2. Admin 登入必須二擇一：
   - 生產建議：設定 `ADMIN_CARD_SERIALS`；
   - break-glass：明確設定 `ENABLE_PASSWORD_FALLBACK=true`，並同時設定強隨機 `ADMIN_USERNAME` / `ADMIN_PASSWORD`。
3. `deploy.sh` 會拒絕空值或公開範本值；確認 `POSTGRES_PASSWORD`、`WEBUI_SECRET_KEY`、`KEYCLOAK_ADMIN_PASSWORD`、OAuth client secret 與 code-server 密碼皆已改成強隨機值。若上游推論服務啟用 API key 驗證，再填入其正式金鑰。受控內網可維持 `ALLOW_INTRANET_MODE=true`，不強制新增 RAG API key。
4. `embed-proxy` 的 HTTP 容器埠是 `7100`；連到 Triton 的 gRPC 埠使用 `EMBED_GRPC_PORT`。

## 4. 套用與驗證

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.hardening.yml \
  up -d --wait

ss -tlnp | grep -E '15432|17100|8043|18088|18080|25678|18443|8200|8300|8088|8443'
```

除 nginx `8088/8443` 外，上述 direct ports 應只出現在 `127.0.0.1`。再執行不啟停容器、不讀取 `.env` 機密的本機驗證：

```bash
./scripts/verify_project.sh
```

該腳本檢查 base/hardening Compose、`iso42001rag` 的 10-service 集合、shell 語法、私鑰工作樹狀態，並執行部署契約、RAG、monitoring、admin pytest（含 runtime regression 與 citation provenance gate 測試）。

## 5. 離線包與回退

- `save_images.sh` / `make_update_package.sh` 的 project/image 名固定為 `iso42001rag`，包含 `admin` image 與 `admin_console/`。
- 程式碼包排除 `.env`、`nginx/ssl/*`、稽核日誌、報告與其他執行期資料。
- 回退 hardening 需在維護視窗移除 override 後重建容器；回退前應先記錄風險接受與替代網路控制。

## 6. 殘餘風險

- 同一 default bridge 仍使維運容器可連內部服務；網段分離與 Docker socket proxy 屬後續架構強化。
- 自簽憑證僅適用於受控開發環境；正式交付應採信任的內部 CA、憑證輪替與安全私鑰權限。
- ReAct 仍是 opt-in 原型；其同步與 SSE 出口已共用 deterministic citation provenance 與 fail-safe，但生產預設仍應保持 `REACT_MODE=false`。
