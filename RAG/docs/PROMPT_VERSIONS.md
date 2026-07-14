# Prompt 基線版本管理

> ISO/IEC 42001 A.4 — AI artifact versioning  
> 對應實作：`rag_system/core/prompts.py:PROMPT_VERSIONS`  
> 對應稽核欄位：`query` 事件的 `prompt_baseline` 與 `prompt_version_hash`<br>
> 最後更新：2026-07-02

---

## 1. 管理原則

外部稽核準備版採 **單一 Prompt 基線版本** 管理，不再對 system prompt、template、
固定回覆訊息分別列版本。這樣稽核時只需要確認一個基線版號、一個 hash、一份變更紀錄。

目前核定基線：

| 項目 | 值 |
|---|---|
| Prompt 基線 | `SYSTEM_PROMPT_BASELINE` |
| 目前版本 | `1.1.0` |
| 稽核欄位 | `prompt_baseline` 與 `prompt_version_hash` |
| RAG API 系統版號 | `rag_system/core/version.py:SYSTEM_VERSION = "1.1.0"` |
| 權責 | 龔修潁（RAG 相關後端） |

`prompt_version_hash` 是對 `PROMPT_VERSIONS` 的 canonical JSON 做 SHA-256。雖然程式內仍有多個
prompt/template 字串，稽核與變更管理只看 `SYSTEM_PROMPT_BASELINE`。
RAG API 的 OpenAPI metadata、`/health` 與 `/v1/models` 也由 `rag_system/core/version.py`
顯示同一個系統版號；Open WebUI 對外模型 id 保持 `rag-agent`，避免既有前端設定漂移。

---

## 2. 升版規則

| 等級 | 何時升 | 範例 |
|---|---|---|
| MAJOR (`x.0.0`) | 回答角色、輸出格式、拒答政策或安全邊界有重大改變 | 改變回答 schema、改變適用法規範圍 |
| MINOR (`0.x.0`) | 新增能力或新增明確處理分支，但仍維持既有行為相容 | 新增 capability 固定回覆、強化特定主題拒答 |
| PATCH (`0.0.x`) | 用字、格式、錯字或不改變行為意圖的微調 | 修正標點、補充範例文字 |

升版時必須：

1. 更新 `rag_system/core/prompts.py` 的 `SYSTEM_PROMPT_BASELINE`。
2. 在本文件 §3 新增一列。
3. 執行 V&V 或內網核定驗證清單，確認無不可接受退化。
4. 產生版本快照並更新 `RAG/CHANGELOG.md`。
5. 記錄新 `prompt_version_hash`。

---

## 3. 基線版本歷程

| 基線版本 | 日期 | 操作者 | 變更摘要 | 驗證 |
|---|---|---|---|---|
| `1.0.0` | 2026-04-13 | 龔修潁（RAG 相關後端） | 初版法律文件助理 prompt 基線，包含檢索回答、拒答、安全攔截、分類與摘要模板 | 初始功能測試 |
| `1.1.0` | 2026-07-02 | 龔修潁（RAG 相關後端） | 統一為單一 Prompt 基線版本；保留強化後的中華民國軍事法規回答、安全拒答、能力說明與中共/大陸相關硬擋規則 | 外部稽核準備基線，啟動後重新產生 V&V |

---

## 4. Hash 對照

| 基線版本 | prompt_version_hash | 說明 |
|---|---|---|
| `1.1.0` | `e61133c0a264b08604706292ba2dbf59b3092e1d9208b1e5c1f971b88c79dc3c` | 對 `{"SYSTEM_PROMPT_BASELINE":"1.1.0"}` 的 canonical JSON 產生 |

---

## 5. 稽核回答口徑

外部稽核若詢問「為何之前看到多個 prompt 版本」，回答如下：

> 舊文件把每個 prompt/template 都分別列版，造成稽核理解成本過高。現行外部稽核準備版已改為單一
> `SYSTEM_PROMPT_BASELINE`，所有 prompt 字串視為同一套受控 AI artifact。任何 prompt 字串調整，
> 都必須升版這個單一基線、重跑驗證並留下 `prompt_version_hash`。
