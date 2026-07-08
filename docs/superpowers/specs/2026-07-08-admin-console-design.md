# ISO 42001 維運管理台（admin console）— 設計文件

- 日期：2026-07-08
- 狀態：已核准（使用者於本日核准）
- 前置決策：RAG/ 凍結已解除（2026-07-08）；不做 Keycloak 角色限制（值域治理走 ISO 稽核 Excel 表單，管理台定位如 Jupyter——內網開發者/管理員入口，不對一般使用者宣傳）
- 補充決策（2026-07-08 稍晚）：**需要簡易帳密登入頁**，避免誤觸 port 即進入。單組帳密存於 gitignored `.env`（`ADMIN_USERNAME`/`ADMIN_PASSWORD`），程式以環境變數讀取、`secrets.compare_digest` 比對、session cookie（記憶體 token，重啟即全登出）；帳密**絕不硬編碼進源碼、測試或 commit**

## 目標

把目前要進後端 cmd 跑的維運操作（model 設定、V&V／RAGAS 評估、regression gate、reindex、告警測試）收進一個網頁管理台，開發者/管理員不再需要 `docker exec` 下指令。

## 已確認的決策

| 決策點 | 結論 |
|---|---|
| 設定生效方式 | 表單寫入 `.env` + 頁面一鍵重啟 rag-api（不改 rag-api 啟動時讀 env 的邏輯） |
| 功能範圍 | 評估類（V&V/online V&V/RAGAS/regression gate/歸因）＋報告檢視與基線比對＋索引維護（reindex/版本快照）＋告警維運（測試告警/SMTP 檢查）＋ model 設定 |
| 部署與暴露 | 獨立 `admin` 容器、宿主 port :8300、不進 nginx |
| 認證/值域 | 簡易帳密登入頁（單組帳密走 .env，非 Keycloak）；白名單鍵 + 表單型別/選項約束值域；治理走 ISO Excel 表單 |
| 同捆改動 | monitoring 告警訊息拆層次（來源標籤＋主訊息＋輔助說明）；手風琴不做 |

## 1. 架構

新容器 **`admin`**（目錄 `admin_console/`，FastAPI + 伺服器端渲染 HTML + 少量 vanilla JS 做表單 POST 與 job 進度輪詢），視覺沿用印刷報告書系統的 token。

掛載：
- `/var/run/docker.sock` — 重啟 rag-api、`docker exec` 跑腳本
- repo 根 `.env`（rw）— 設定寫入
- `RAG/data/reports/`（ro）— 報告檢視
- `admin_console/data/`（rw，git-ignored）— jobs.jsonl、changes.jsonl

**腳本執行採 `docker exec` 到原生容器**：評估類到 `ISO42001_monitoring`（`python3 scripts/run_extended_vv.py` 等）、索引類到 `ISO42001_rag_api`（`python3 scripts/reindex.py`、`version_tracker.py`）。admin 鏡像不複製任何評估依賴（否決替代案：依賴複製會漂移、鏡像肥大）。

## 2. 頁面結構（單頁四區塊）

1. **Model 設定**：白名單 10 鍵表單——`CHAT_MODEL_NAME`、`TOP_K`、`RERANK_TOP_N`、`REASONING_EFFORT`、`REACT_MODE`、`CHUNK_SIZE`、`MAX_RETRIEVAL_TOKENS`、`RATE_LIMIT_PER_MINUTE`、`RAG_LOG_LEVEL`、`RAG_LOG_VERBOSE`。每鍵依型別渲染（數字/布林/枚舉/字串），並列「`.env` 值 vs 容器內生效值」（`docker inspect` 取得），不一致標「已寫入，待重啟」。`CHUNK_SIZE` 變更附「需 reindex」警示並連到索引區塊。儲存→寫 .env→〔重啟 rag-api 套用〕→輪詢 rag-api `/health` 顯示恢復。金鑰與連線 URL 類**不進 UI**。
2. **評估操作**：五個一鍵按鈕（extended V&V、online V&V、RAGAS、regression gate、歸因分析），背景 job、全域互斥（同時僅一個），顯示執行狀態與 stdout 尾段。
3. **報告檢視**：列出 `RAG/data/reports/` 歷次 vv/ragas 報告（時間、Hit Rate、樣本數摘要）；任兩份報告 per-query flip 比對（對應既有的版本比較方法）。
4. **索引與告警**：reindex＋版本快照觸發（同 job 模型）；測試告警（呼叫 monitoring `/v1/alerts/test`）、SMTP 啟用狀態顯示。

## 3. Job 模型與留痕

- Job：asyncio 子行程執行 `docker exec`，狀態機 `queued → running → done|failed`，狀態與輸出落 `jobs.jsonl`；頁面每 2 秒輪詢 `/jobs/{id}`。非零退出→FAILED 附 stderr 尾段。
- 留痕：每次設定變更（鍵、前值→後值、時間）與每次 job 觸發追加 `changes.jsonl`。非權限管制，係供 ISO Excel 表單抄錄的機器紀錄。
- `.env` 寫入：讀-改-寫，只動白名單鍵，保留註解與未管理鍵；寫入前留 `.env.bak` 一份。

## 4. monitoring 端同捆改動

`_render_alerts_table` 與 SSE `buildRow` 拆層次：來源標籤（pill 樣式）＋主訊息（粗體）＋輔助說明（12px 灰字）。monitoring 不加任何寫權/docker 權，維持唯讀稽核定位。

## 5. 錯誤處理

- docker sock 不可用／目標容器不存在 → 頁面明示錯誤，不吞。
- `.env` 寫入失敗（權限/磁碟）→ 顯示錯誤且不觸發重啟。
- 重啟後 `/health` 60 秒未恢復 → 顯示警示與 `docker logs` 提示（不自動回滾，回滾由管理員以 `.env.bak` 手動處理）。
- job 執行中重複觸發 → 拒絕並顯示目前 job。

## 6. 測試

- pytest（admin_console/tests/）：.env 讀改寫（白名單、保註解、bak）、job 狀態機、頁面渲染煙霧測試、changes.jsonl 追加；`docker exec` 以可注入的命令替身（fake runner）測。
- monitoring 告警層次：更新 `test_dashboard_render.py` 對應斷言。
- 實機驗證：Playwright 走一次「改 TOP_K→重啟→生效值一致」與「觸發 regression gate→看到結果」。

## 7. 範圍外

認證/角色、多人並發鎖（單管理員假設）、值域治理流程、告警手風琴、rag-api 熱套用設定、nginx 路由變更。

## 8. compose 變更

`docker-compose.yaml` 新增 `admin` 服務：build `./admin_console`、`ports: "8300:8300"`、上述掛載、`restart: unless-stopped`、depends_on 無硬依賴（monitoring/rag-api 不在時對應操作報錯即可）。
