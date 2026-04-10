# ISO42001 RAG 系統 API 介接文件 (測試團隊專用)

本文件提供 ISO42001 RAG 系統的 API 介接規範，供測試團隊進行功能測試、效能測試及整合測試。

---

## 1. 連線資訊 (Connection Info)

*   **API 進入點 (Endpoint):** `http://<伺服器IP>:8000/v1/chat/completions`
    *   *註：若透過 Nginx 轉發，通常為 `https://<網域名稱>/v1/chat/completions`*
*   **HTTP 方法:** `POST`
*   **Content-Type:** `application/json`

## 2. 身分驗證與對話管理 (Auth & Session)

*   **API Key (Authorization):** 
    *   目前內部測試 API 採 OpenAI 相容格式。
    *   Header: `Authorization: Bearer dummy-key` (可填寫任意字串)。
*   **Session ID (追蹤對話):** 
    *   為了維持對話上下文 (Multi-turn conversation)，請於 Header 帶入自訂的 Session ID。
    *   Header: `x-session-id: <UUID_OR_ANY_ID>`
    *   *若未帶入，系統將會自動為該次請求產生一個新的隨機 Session ID。*

## 3. 資料格式 (Data Transfer Object)

### 3.1 Request Payload (請求格式)
採用 OpenAI Chat Completion 標準格式。

```json
{
  "model": "rag-agent",
  "messages": [
    {
      "role": "system",
      "content": "你是一個法律專家助手。"
    },
    {
      "role": "user",
      "content": "請說明 ISO 42001 的核心要求有哪些？"
    }
  ],
  "stream": false,
  "temperature": 0.0
}
```

| 欄位名稱 | 類型 | 必填 | 說明 |
| :--- | :--- | :--- | :--- |
| `model` | string | 是 | 固定填寫 `rag-agent`。 |
| `messages` | array | 是 | 對話歷史清單，包含 `role` (system/user/assistant) 與 `content`。 |
| `stream` | boolean | 否 | 預設為 `false`。若設為 `true` 則採 Server-Sent Events (SSE) 串流輸出。 |
| `temperature` | float | 否 | 預設 `0.0`。數值越高回應越隨機，測試建議固定為 `0.0`。 |

### 3.2 Response Payload (回應格式)

```json
{
  "id": "chatcmpl-xxxx-xxxx",
  "object": "chat.completion",
  "created": 1711234567,
  "model": "rag-agent",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "[這是 RAG 生成的法律回覆文字...]"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

## 4. 系統配置與模型參數 (LLM & RAG Config)

這些參數定義了系統的行為邊界，測試團隊可參考此數值進行邊界測試：

*   **核心模型 (Models):**
    *   **Chat:** `gpt-oss-20b` (OpenAI 格式對接)
    *   **Embedding:** `nvidia/nv-embed-v2`
*   **RAG 檢索配置:**
    *   **語意切片大小 (Chunk Size):** 1000 字元/Tokens。
    *   **檢索取回數量 (TOP_K):** 5 組最相關切片。
    *   **檢索 Token 預算 (Max Retrieval Tokens):** 3000 Tokens (檢索內容若超過此長度會進行截斷，確保模型不爆掉)。
*   **記憶機制:**
    *   **歷史紀錄上限:** 保留最近 50 次對話訊息 (依據 `session_id` 判別)。

## 5. 錯誤處理 (Error Handling)

| HTTP Status | 說明 |
| :--- | :--- |
| `200` | 請求成功。 |
| `400` | 格式錯誤 (例如 messages 清單中沒有 user 的問題)。 |
| `404` | Endpoint 路徑錯誤。 |
| `500` | 伺服器內部錯誤 (例如資料庫連線中斷、LLM 服務未啟動)。 |

---
*文件更新日期：2026-03-26*
