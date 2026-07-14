# 內網歷史資料遷移手冊

> 適用系統：ISO 42001 中文法律 RAG 系統<br>
> 適用情境：先以空資料啟動新服務，再載入既有內網歷史<br>
> 文件版本：1.0<br>
> 更新日期：2026-07-14

## 1. 目的與原則

本流程將既有內網系統的對話、帳號、向量索引、稽核、監控與管理留痕，載入已完成煙霧測試的新服務。

核心原則：

- 新服務在載入歷史前只能使用測試帳號與測試資料，不得先投入正式使用。
- 遷移採「完整備份後整庫替換」，不直接合併兩套 OpenWebUI SQLite、Keycloak 資料、PostgreSQL 或 audit JSONL。
- 備份與還原期間停止所有資料寫入端，避免取得不一致快照。
- 還原前先自動備份目標機，任何失敗都保留回退材料。
- 目標機一律保留目前新版 `.env` 與 TLS；進內網後只調整 IP、主機名稱及端點，不從舊系統還原設定。
- 帳號、對話及稽核備份只能使用核准的加密媒體傳輸。

## 2. 資料範圍

| 資料 | 持久化位置 | 遷移方式 |
|---|---|---|
| RAG 對話、向量 collection 與 embedding | `iso42001rag_pgdata` | `pg_dump`／`pg_restore` |
| OpenWebUI 使用者、對話、訊息、設定與附件 | `iso42001rag_openwebui_data` | 停服後完整 volume 封存／替換 |
| Keycloak realm、使用者、client 與認證資料 | `iso42001rag_keycloak_data` | 停服後完整 volume 封存／替換 |
| 稽核日誌、docstore、版本與 runtime 狀態 | `RAG/data/` | bind runtime 封存／替換 |
| 告警、可用率、V&V 與監控報告 | `monitoring_addon/data/` | bind runtime 封存／替換 |
| 管理操作、工作紀錄與設定備份 | `admin_console/data/` | bind runtime 封存／替換 |
| 新版內網連線、OAuth secret、加密 secret、TLS | 目標機 `.env`、`nginx/ssl/` | **不遷移、不覆蓋**；只在目標機調整 IP／主機名稱 |

只搬專案資料夾不會包含 Docker named volumes。`full-stack-code.zip` 也刻意排除上述執行期資料與機密，只能用來更新程式碼，不能取代 runtime 備份。

### 2.1 外網與內網資料隔離

外網開發機只能提供「乾淨版本的程式碼」與「離線 Docker images」，不得將外網 runtime 資料一併帶入內網。不要直接對外網開發目錄執行無排除條件的 `cp -a`、`scp -r` 或 `rsync`。

進內網前應確認：

- image 總包只有 `IMAGE_MANIFEST.txt` 與 10 個 `images/*.tar`，不含專案目錄。
- `rag-api` image 建置時由 `RAG/.dockerignore` 排除 `RAG/data/`、`.env*`、憑證、私鑰、快取與開發產物；`save_images.sh` 會再以無網路暫存容器檢查，不符合就拒絕產包。
- 不傳輸外網的 `RAG/data/`、`monitoring_addon/data/`、`admin_console/data/`、`runtime_backups/`、`.env` 或 `nginx/ssl/`。
- 新版 `.env` 在內網目標機保留，只修改內網 IP、主機名與端點；TLS 憑證也在內網產生或由正式機密流程配發。
- 歷史備份只能在「舊內網系統」產生，然後依本手冊還原到新版；不可使用外網開發機的 runtime 目錄當作歷史來源。

## 3. 前置條件

來源與目標應先確認：

- Compose project 名稱均為 `iso42001rag`。
- PostgreSQL、OpenWebUI、Keycloak 使用與備份相容的 image 版本。
- 目標機已載入完整離線 images，且有足夠空間同時容納來源備份與自動回退備份。
- 新服務的 HTTPS、Keycloak、`rag-agent`、監控與稽核已用測試資料驗證。
- 遷移時間已公告，正式使用者停止操作。

若日後需要跨大版本升級，應先用舊版本 image 還原成功，再逐一升級服務；不要同時進行資料搬遷與跨版本 schema 變更。

### 3.1 同一台主機以兩個目錄切換版本

假設舊版與新版分別位於：

```text
/home/ISO42001/ISO42001Deploy
/home/ISO42001/ISO42001Deploy_v1.1
```

目前 Compose 固定使用 project 名 `iso42001rag`、固定容器名稱及同一組 named volumes，因此兩個目錄在同一台 Docker 主機上**不是兩套可同時運行的服務**。從新版目錄執行 `docker compose up` 會接管／重建同一組容器，而不是另外建立一套空白環境。

備份必須以舊目錄為專案根目錄。若遷移工具只存在新版目錄，先複製備份與驗證工具：

```bash
cp /home/ISO42001/ISO42001Deploy_v1.1/backup_runtime.sh \
   /home/ISO42001/ISO42001Deploy/
cp /home/ISO42001/ISO42001Deploy_v1.1/verify_runtime_migration.sh \
   /home/ISO42001/ISO42001Deploy/
chmod +x /home/ISO42001/ISO42001Deploy/{backup_runtime.sh,verify_runtime_migration.sh}

cd /home/ISO42001/ISO42001Deploy
./backup_runtime.sh \
  --output /home/ISO42001/ISO42001Deploy_v1.1/runtime_backups/pre-v1.1
```

不要在 `ISO42001Deploy_v1.1` 直接執行來源備份：named volumes 雖然相同，但 `RAG/data`、monitoring 與 admin 是 bind mount，會因此讀到新版目錄而漏掉舊目錄歷史。

備份完成後的同主機切換順序：

```bash
# 1. 停止舊容器；禁止加 -v
cd /home/ISO42001/ISO42001Deploy
docker compose stop

# 2. 使用新版 .env，只調整內網 IP／主機名稱／端點
cd /home/ISO42001/ISO42001Deploy_v1.1
nano .env

# 3. 只啟動新版目錄下的 db，讓還原工具建立目標回退備份
docker compose up -d --no-build --pull never db

# 4. 載入舊歷史；完成後工具會啟動新版完整 stack
./restore_runtime.sh \
  --backup /home/ISO42001/ISO42001Deploy_v1.1/runtime_backups/pre-v1.1
```

若必須先以「完全空白且獨立」的新服務進行煙霧測試，應使用另一台主機／VM；同一主機則必須另做一套不同 project、容器名稱、ports 與 volumes 的測試 Compose，目前正式 Compose 不提供這種雙開模式。

## 4. 在舊內網來源機建立完整備份

於舊系統專案根目錄執行：

```bash
./backup_runtime.sh
```

工具會：

1. 記錄備份前正在運行的服務。
2. 暫停 OpenWebUI、RAG、Keycloak、Jupyter、Monitoring 與 Admin。Code Server 保持運行，避免從其終端執行時中斷備份程序。
3. 產生不含內容的來源資料摘要 `source-snapshot.json`。
4. 使用 PostgreSQL 邏輯 dump 備份 RAG 對話與向量索引。
5. 冷備份 OpenWebUI 與 Keycloak volumes。
6. 封存 `RAG/data`、monitoring 與 admin 執行期資料。
7. 產生 `SHA256SUMS`，並重新啟動備份前原本運行的服務。

預設輸出：

```text
runtime_backups/runtime-YYYYMMDD-HHMMSS/
├── SHA256SUMS
├── backup-info.txt
├── source-snapshot.json
├── postgres.dump
├── openwebui-data.tar.gz
├── keycloak-data.tar.gz
├── bind-runtime.tar.gz
└── running-services.txt
```

傳輸前再次驗證：

```bash
cd runtime_backups/runtime-YYYYMMDD-HHMMSS
sha256sum -c SHA256SUMS
```

整個目錄含個資、對話與稽核證據，不得放入 Git、一般共享資料夾或未加密媒體；來源 `.env` 與 TLS 不會包含在備份中。

## 5. 目標新服務載入歷史

先將完整備份目錄放到目標機核准位置，再回到新服務專案根目錄：

```bash
./restore_runtime.sh --backup /核准路徑/runtime-YYYYMMDD-HHMMSS
```

預設行為：

- 先驗證來源 `SHA256SUMS`。
- 要求輸入 `RESTORE-RUNTIME`，避免誤操作。
- 自動建立 `runtime_backups/pre-restore-*` 目標機回退備份。
- 替換 PostgreSQL、OpenWebUI、Keycloak 與三個 bind runtime 目錄。
- 保留目標機目前新版 `.env` 與 TLS，完全不從來源備份覆蓋。
- 在服務啟動前以 `--compare-exact` 比對冷資料筆數與 volume／稽核摘要，確保解壓內容逐項一致。
- 精確比對後只移除還原資料庫內舊的 OpenWebUI `openai.api_base_urls`、`openai.api_keys`、`openai.api_configs` 與 `webui.url`；使用者、對話、權限及其他 UI 設定保持不變，正式啟動時改讀新版 `.env`／Compose。
- 呼叫 `deploy.sh` 同步 runtime 設定並啟動完整服務。
- 自動比對來源與目前的資料筆數、檔案數與稽核摘要。

進入內網後，先在目標新版 `.env` 調整 IP、主機名稱及端點，再執行還原。管理者應確認：

- `LLM_HOST`、`LLM_API_BASE`、`EMBED_GRPC_PORT`
- `WEBUI_URL`
- `OPENID_PROVIDER_URL`
- `OAUTH_CLIENT_SECRET`
- Keycloak realm/client redirect URI

如果還原後 Keycloak client secret 與新版 `.env` 的 `OAUTH_CLIENT_SECRET` 不一致，應在 Keycloak 管理端將 client secret 對齊新版 `.env`；不要把舊 `.env` 載回來。`WEBUI_SECRET_KEY` 也維持新版值，既有瀏覽器工作階段可能需要重新登入，但不應因此刪除 OpenWebUI 歷史對話。

OpenWebUI 會將部分 `ConfigVar` 保存在 `webui.db`，可能優先於環境變數。因此還原工具會在驗證來源冷資料完整後清除上述四組舊連線欄位，避免舊 IP 蓋過新版 `.env`；此步驟不會清除聊天或帳號。

需要先檢查資料而不啟動服務時：

```bash
./restore_runtime.sh \
  --backup /核准路徑/runtime-YYYYMMDD-HHMMSS \
  --no-start
```

確認後執行 `./deploy.sh`，再單獨執行遷移比較。

## 6. 自動與人工驗收

自動比較：

```bash
./verify_runtime_migration.sh \
  --compare /核准路徑/runtime-YYYYMMDD-HHMMSS/source-snapshot.json
```

工具只讀取資料筆數、檔案數及稽核摘要，不輸出對話、帳號或稽核內容。至少檢查：

- PostgreSQL `conversations`、collection、embedding 筆數不得少於來源。
- OpenWebUI 使用者、對話與訊息筆數不得少於來源。
- 稽核檔案及行數不得少於來源；行數相同時，內容摘要必須相同。
- Keycloak 與 OpenWebUI volume 不得為空。
- monitoring 與 admin runtime 檔案數不得少於來源。

`restore_runtime.sh` 會在啟動服務前自動執行更嚴格的冷資料精確比較；一般維運只需在啟動後使用上述 `--compare`，允許新服務正常追加 session、稽核及監控狀態。

自動比較通過後仍須人工確認：

- [ ] 舊 Keycloak 帳號可以登入。
- [ ] OpenWebUI 顯示該使用者的舊對話。
- [ ] 舊對話可以正常開啟，且新問題會追加而非覆蓋。
- [ ] `rag-agent` 能使用既有向量索引回答已收錄法規。
- [ ] 最新 audit JSONL 的雜湊鏈驗證為 `valid=True`。
- [ ] monitoring 顯示舊可用率、告警與 V&V 報告。
- [ ] `.env`、TLS 與備份檔案權限符合單位規範。
- [ ] 操作者、時間、來源 SHA-256、結果與異常已記錄。

## 7. 回退

還原開始前，工具會在目標機建立：

```text
runtime_backups/pre-restore-YYYYMMDD-HHMMSS/
```

若來源歷史載入失敗，可使用相同還原工具載回該目錄：

```bash
./restore_runtime.sh \
  --backup runtime_backups/pre-restore-YYYYMMDD-HHMMSS
```

回退本身也是取代操作，仍會要求確認並再建立一份操作前備份。不要刪除失敗現場、來源備份或回退備份，直到驗收與變更紀錄正式結案。

## 8. 禁止事項

- 不得執行 `docker compose down -v`。
- 不得執行 `docker volume prune`。
- 真實歷史產生後不得執行 `reset_data.sh`。
- 不得把兩套 `webui.db`、Keycloak H2 或同日期 audit JSONL 直接串接／覆寫合併。
- 不得把 `runtime_backups/`、`.env`、TLS 私鑰或使用資料提交到 Git。
- 不得用來源舊 `.env` 覆蓋目標新版 `.env`。
- 不得在服務仍接受正式請求時複製 SQLite/H2 volume。

## 9. 工具說明

```bash
./backup_runtime.sh --help
./restore_runtime.sh --help
./verify_runtime_migration.sh --help
```

腳本不會自動刪除任何備份。備份保存期限、離線副本、加密、存取授權與銷毀程序應依單位資料治理規範執行。
