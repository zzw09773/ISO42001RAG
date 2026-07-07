# 模型卡（Model Card）— ISO 42001 RAG 法律文件查詢系統

> ISO 42001 A.4（AI 系統生命週期文件化）證據。
> 狀態：v1.1.0（2026-06-10 編製，待審查負責人核定）

## 1. 系統概述

| 欄位 | 內容 |
|---|---|
| 系統名稱 | ISO 42001 RAG 法律文件查詢系統 |
| 系統版本 | v1.1.0（2026-06-08，見 `RAG/CHANGELOG.md`） |
| 用途 | 國軍法律承辦人於內網查詢軍事法規條文（AI 輔助，非法律建議） |
| 部署環境 | 內網 10.53.100.12（開發驗證環境：172.16.120.35 後端） |
| 系統形態 | Retrieval-Augmented Generation（RAG），LangGraph 工作流 |

## 2. 模型組件

| 組件 | 模型 | 服務方式 | 備註 |
|---|---|---|---|
| 生成 LLM | `openai/gpt-oss-20b` | vLLM（OpenAI 相容 API） | reasoning model，自動帶 `reasoning_effort=medium`（可由 `REASONING_EFFORT` 覆寫） |
| Embedding | `nvidia/NV-embed-V2` | Triton gRPC + embed-proxy 轉換層 | 非對稱設計：query 端加英文指令 prefix，文件端不加 |
| 向量庫 | PostgreSQL + pgvector | 容器 `ISO42001_pgvector` | |

兩個模型皆為**預訓練模型推論使用，本專案未進行任何訓練／微調**；
不存在本專案的訓練資料集。

## 3. 知識庫與檢索

| 項目 | 內容 |
|---|---|
| 知識庫 | 軍事法規條文（Markdown），Article-Aware Chunking（以「條」為切分單位） |
| 檢索策略 | 向量檢索 + HyDE 雙路徑（僅抽象查詢）+ Self-Query 跨法過濾 + LLM 重排 |
| 工作流 | classify → retrieve → generate → verify（LangGraph）；另有 ReAct 模式（`REACT_MODE`，預設關閉） |

## 4. 預期使用與限制

**預期使用**：法律承辦人輸入中文法律問題，系統檢索相關條文並生成附引用的回答。

**明確不適用**：
- 非法規範圍的問題（系統依 `SAFETY_CONTROLS.md` 規則拒答）
- 作為法律決定的唯一依據（輸出為 AI 輔助資訊，最終判斷由承辦人負責）

**已知限制**：
- 生成內容可能與條文不一致（幻覺）——以 verify 節點、引用標註與人工查證緩解
- 知識庫未涵蓋的法規無法回答
- 後端推論伺服器不可用時系統降級（fallback 行為見 audit log `actions` 欄位）
- 偏誤風險——以 `tests/evaluation/test_bias_fairness.py`（8 案例）定期檢測

## 5. 評估結果（v1.1.0）

| 指標 | 值 | 證據 |
|---|---|---|
| Hit Rate（唯一 gating，目標 ≥ 0.90） | **0.9355** | 歷史 V&V 報告；外部稽核準備基線需重新產生 |
| Recall@K | 0.9032 | 同上（informational） |
| Precision@K | 0.7231 | 同上（informational） |
| 拒答正確率（out-of-scope） | 1.0 | 同上 |
| 評估資料集 | 34 筆 golden dataset（31 筆含 ground truth） | 內網基線需由權責人員確認資料集版本 |

## 6. 安全與稽核

- 安全控制：`RAG/docs/SAFETY_CONTROLS.md`（9 道守則、8 種威脅偵測）
- 稽核日誌：`RAG/docs/AUDIT_LOG_SCHEMA.md`（SHA-256 雜湊鏈防竄改）
- Prompt 版控：`RAG/docs/PROMPT_VERSIONS.md`（單一 `SYSTEM_PROMPT_BASELINE` + hash）

## 7. 版本與變更

版控機制：SHA-256 快照（`RAG/data/versions/snapshot_*.json`）+ tar.gz 備份 +
`RAG/CHANGELOG.md`（Keep a Changelog 格式）。內網環境無 Git，此為 A.6.2.5
變更管理的正式機制。
