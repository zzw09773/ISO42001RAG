# ISO 42001 中文法律 RAG 系統

本專案將中文法律 RAG API、Open WebUI、OIDC 認證、稽核監測與內網維運工具整合為可離線部署的 Docker Compose stack。模型不包在專案內；`rag-api` 透過內網 LLM HTTP 端點與 `embed-proxy` 連接 GPU 推論主機。

## 服務架構

`docker-compose.yaml` 定義 10 個服務：

| 服務 | 用途 | 預設宿主埠 |
|---|---|---|
| `db` | PostgreSQL 17 + pgvector | `15432` |
| `embed-proxy` | OpenAI Embeddings HTTP（容器 `7100`）轉 Triton gRPC（`EMBED_GRPC_PORT`） | `17100` |
| `rag-api` | FastAPI RAG、稽核與 OpenAI 相容 API | `8043` |
| `openwebui` | 使用者對話介面 | `18088` |
| `keycloak` | OpenWebUI OIDC 與強密碼政策 | `18080` |
| `nginx` | OpenWebUI 與 monitoring 的 HTTPS 反向代理 | `8088` / `8443` |
| `jupyter` | 法規索引與開發環境 | `25678` |
| `code-server` | 內網維運 IDE | `18443` |
| `monitoring` | 健康、漂移、V&V 與稽核儀表板 | `8200` |
| `admin` | 憑證卡登入的維運管理台 | `8300` |

```text
使用者 ─HTTPS→ nginx ─→ OpenWebUI ─→ rag-api
                                  ├→ db / pgvector
                                  ├→ embed-proxy:7100 ─gRPC→ Triton
                                  └→ 內網 LLM HTTP
                    └→ monitoring

OpenWebUI ─OIDC→ Keycloak
維運人員 ─憑證卡→ admin:8300
```

`admin` 不經 nginx，以一次性 nonce 綁定的 PKCS#7/CMS 簽章、釘選 CA 憑證鏈與 `ADMIN_CARD_SERIALS` 白名單登入。帳密 fallback 預設關閉；若作為 break-glass 啟用，必須同時設定強密碼。

Admin 儲存設定時仍維護根 `.env`，但只把 13 個可管理的非秘密鍵同步到 `admin_console/data/rag-runtime.env`；rag-api 以唯讀方式載入此白名單檔後 restart。`rag-effective.env` 也只記錄同一組 13 鍵的實際生效值。這兩份 runtime 檔均不含 `API_KEYS`、`LLM_API_KEY`、`EMBED_API_KEY` 或管理密碼；但 rag-api 連接上游推論服務所需的 `LLM_API_KEY` / `EMBED_API_KEY` 仍會由 Compose `environment` 單獨注入容器，不應解讀為 RAG 容器內永遠沒有 API key。

## RAG 執行模式與安全邊界

- 生產預設是 classic LangGraph：`classify → retrieve → generate → verify`。
- ReAct 是 opt-in 原型；只有明確設定 `REACT_MODE=true` 才啟用。
- classic `verify` 先執行確定性 citation provenance gate：回答中的「第 N 條」必須可回溯至 `retrieved_sources`；無據引用先重試，重試額度耗盡時以 fail-safe 訊息取代原回答，不會將無據條號送給使用者。
- `stream=true` 保留 OpenAI SSE envelope，但答案會先完整緩衝並通過輸出過濾，再送給客戶端。

## 部署

1. 建立設定，並將所有佔位密碼改為強隨機值：

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

2. 確認至少已設定 LLM、Embedding、DB、OpenWebUI/Keycloak 與 code-server。Admin 登入必須二擇一：填入 `ADMIN_CARD_SERIALS`，或作為 break-glass 明確設定 `ENABLE_PASSWORD_FALLBACK=true` 並同時填入強隨機 `ADMIN_USERNAME` / `ADMIN_PASSWORD`；兩者皆空時 admin 會 fail closed。`embed-proxy` 的 HTTP 容器埠固定為 `7100`，Triton gRPC 埠由 `EMBED_GRPC_PORT` 設定。

   本專案目前採受控內網與 port 直連，可維持 `ALLOW_INTRANET_MODE=true`；本次修正不要求設定 RAG API key。

3. 啟動完整 stack：

   ```bash
   ./deploy.sh
   ```

   `deploy.sh` 專為離線現場設計，會使用 `--no-build --pull never`，不會臨場 build 或 pull。乾淨 checkout 必須先放入完整 `images/*.tar`，或先在可建置的機器執行 `make_update_package.sh`；缺任一核定 image 時腳本會直接結束。Compose `--wait` 只會等有 healthcheck 的服務成為 healthy，沒有 healthcheck 的服務只能確認容器處於 running；這不等同端到端 RAG 查詢或上游模型已驗證就緒。

### 部署強化

主 Compose 保留開發可用性；正式內網交付應合併 hardening override：

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.hardening.yml \
  up -d --wait
```

hardening 會將 DB、RAG、Embedding、OpenWebUI、Keycloak、Jupyter、code-server、monitoring 與 `admin:8300` 的 direct ports 限制為 loopback。`admin` 具有 Docker 控制與 `.env` 維護能力，不可對不可信網路暴露。完整風險與驗證步驟見 `RAG/docs/governance/DEPLOYMENT_HARDENING.md`。

### TLS 私鑰原則

本次工作樹已將曾被追蹤的 `nginx/ssl/cert.key` / `cert.crt` / `cert.csr` 標記刪除；這份刪除必須納入交付 commit，新 revision 才不會再夾帶它們。舊私鑰仍存在 Git history，必須視為已洩漏：不可再用於任何環境，下次部署須以 `nginx/generate_certs.sh` 或正式機密配發流程產生新憑證並完成輪替。若對外分享含歷史的 repository clone，還需另行評估歷史清理；本次不主張 Git history 已乾淨。

## 離線包

```bash
./save_images.sh
./make_update_package.sh
```

離線輸出包含 10 個服務所需的 images，並包含 `admin_console/`、Compose、hardening override、`tests/`、設定範本與文件。Compose project/image 名固定為 `iso42001rag`，`MANIFEST.txt` 對實際交付的 zip/tar 記錄 SHA-256，因此可辨識與驗證某一份具體成品。這不代表任意時間重建都會產生相同 bytes：`pgvector/pgvector:pg17` 與 `nginx:alpine` 等浮動 tag 可能在日後指向不同內容，若要達成 rebuild 級別的可重現性，必須改用受控 mirror 或 digest pin。`.env`、TLS 私鑰、稽核日誌與其他執行期資料一律排除。

## 驗證

不啟停容器、不讀取或印出 `.env` 機密的本機驗證入口：

```bash
./scripts/verify_project.sh
```

它會檢查：

- base 與 hardening Compose 可解析，且服務集合為預期的 10 個；
- 版控中沒有 nginx TLS 私鑰；
- 專案 shell scripts 通過 `bash -n`；
- 部署契約測試 `tests/` 涵蓋固定 image 名、admin hardening、離線包與憑證權限，並會在無 Git metadata 的離線包中照常執行；
- `RAG/tests`、`monitoring_addon/tests`、`admin_console/tests` 三套 pytest。

若只想手動檢查 Compose：

```bash
ADMIN_CARD_SERIALS=0000000 docker compose --env-file .env.example config --quiet
ADMIN_CARD_SERIALS=0000000 docker compose --env-file .env.example \
  -f docker-compose.yaml -f docker-compose.hardening.yml config --quiet
```

`scripts_md2html.py` 會將 Markdown 批次產生自包含 HTML；Markdown 是版控來源，發佈前應重生 HTML 鏡像並將其與同一 revision 一併驗證。

## 資料清理

`reset_data.sh` 會刪除稽核日誌、對話與監測執行期資料。它只能在正式測試開始前使用；測試開始後，這些資料屬稽核證據，不得重置。
