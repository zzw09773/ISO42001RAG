# RAG 系統 Token 優化指南

> **Note:** The **Two-Stage Retrieval (Strategy 4)** described below has been implemented in the production system. See [OPTIMIZATION_CODE.md](./OPTIMIZATION_CODE.md) for the actual source code and implementation details.

## 問題診斷

當前配置導致較高的 token 消耗:
- **Parent chunk size**: 2000 字元
- **Top-k retrieval**: 5 個文件
- **單次查詢 token 消耗**: ~2,500-3,000 tokens (只是檢索內容)

## 優化策略

### 1. **降低 Parent Chunk Size** (推薦)

**修改位置**: [rag_service.py:95](../rag_system/rag_service.py#L95)

```python
# 原始設定
self.parent_splitter = RecursiveCharacterTextSplitter(
    chunk_size=2000,  # 太大
    chunk_overlap=200,
    separators=["\n第", "\n\n", "\n", "。", " ", ""]
)

# 優化建議
self.parent_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,  # 減半 → 節省 50% token
    chunk_overlap=100,
    separators=["\n第", "\n\n", "\n", "。", " ", ""]
)
```

**效果**:
- Token 消耗: 2,500 → 1,250 tokens (減少 50%)
- 精確度: 略降,但通常足夠

---

### 2. **降低檢索數量 (top_k)**

**修改位置**: [config.py:18](../rag_system/config.py#L18)

```python
# 原始設定
DEFAULT_TOP_K = 5

# 優化建議
DEFAULT_TOP_K = 3  # 減少到 3 個
```

**或在查詢時動態設定**:
```python
# 環境變數
export TOP_K=3

# 或在程式中
config = RAGConfig(top_k=3)
```

**效果**:
- Token 消耗: 2,500 → 1,500 tokens (減少 40%)
- 精確度: 略降,但 3 個文件通常已足夠

---

### 3. **內容截斷** (激進優化)

在檢索後進一步截斷內容。

**新增工具函數**:
```python
# rag_system/utils.py (新檔案)
def truncate_content(content: str, max_length: int = 500) -> str:
    """截斷內容保留關鍵部分"""
    if len(content) <= max_length:
        return content

    # 智能截斷:保留開頭和找到的關鍵句
    return content[:max_length] + "...(已截斷)"
```

**在檢索工具中使用**:
```python
# rag_system/tool/retrieve.py
from ..utils import truncate_content

documents = retriever.get_relevant_documents(question)
for doc in documents:
    doc.page_content = truncate_content(doc.page_content, max_length=600)
```

**效果**:
- Token 消耗: 進一步減少 30-50%
- 精確度: 可能降低,需測試

---

### 4. **使用摘要檢索 + 細節檢索** (兩階段)

**[IMPLEMENTED]** 先用摘要檢索找到相關文件,再只檢索該文件的細節。

**實作方式** (需修改架構):
1. 第一階段: 檢索 top_k=10 個摘要 (Child Chunks)
2. 第二階段: 使用 LLM Rerank 選出最相關的 1 個
3. 第三階段: 檢索該摘要對應的完整母文件 (Parent Document)

**效果**:
- Token 消耗: 大幅減少 60-70% (Context 僅需 1 個完整文件)
- 精確度: 提高 (因為第一階段漏斗擴大到 10)
- 複雜度: 已封裝於 `RAGService.query`

---

### 5. **動態 Token 預算**

根據查詢複雜度動態調整檢索數量。

```python
def get_adaptive_top_k(question: str) -> int:
    """根據問題長度動態調整 top_k"""
    if len(question) < 20:
        return 2  # 簡單問題
    elif len(question) < 50:
        return 3  # 中等問題
    else:
        return 5  # 複雜問題
```

---

## 推薦配置

### 🔵 保守優化 (精確度優先)
```python
parent_chunk_size = 1500    # 減少 25%
child_chunk_size = 600      # 減少 25%
top_k = 4                   # 減少 1 個
```
**Token 節省**: ~30%

### 🟢 平衡優化 (推薦)
```python
parent_chunk_size = 1000    # 減少 50%
child_chunk_size = 500      # 減少 37.5%
top_k = 3                   # 減少 40%
```
**Token 節省**: ~60%

### 🟡 激進優化 (速度優先)
```python
parent_chunk_size = 800     # 減少 60%
child_chunk_size = 400      # 減少 50%
top_k = 2                   # 減少 60%
content_max_length = 500    # 額外截斷
```
**Token 節省**: ~75%

---

## 實施步驟

### 快速測試 (無需修改程式碼)

使用環境變數:
```bash
# .env
TOP_K=3
CONTENT_MAX_LENGTH=600
```

### 永久修改

1. **修改 [config.py](../rag_system/config.py)**:
```python
DEFAULT_TOP_K = 3          # 從 5 改為 3
DEFAULT_CONTENT_MAX_LENGTH = 600  # 從 800 改為 600
```

2. **修改 [rag_service.py](../rag_system/rag_service.py)**:
```python
self.parent_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,      # 從 2000 改為 1000
    chunk_overlap=100,    # 從 200 改為 100
    ...
)

self.child_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,       # 從 800 改為 500
    chunk_overlap=50,     # 從 100 改為 50
)
```

3. **重新索引**:
```bash
# 清空舊索引
python scripts/debug/clear_data.py --execute --docstore

# 重新建立索引
python scripts/reindex.py
```

---

## 效果監控

建立監控腳本 `scripts/measure_tokens.py`:

```python
import tiktoken

def count_tokens(text: str, model: str = "gpt-4") -> int:
    """計算文本的 token 數量"""
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(text))

def measure_retrieval_cost(documents):
    """測量檢索成本"""
    total_tokens = sum(count_tokens(doc.page_content) for doc in documents)
    print(f"檢索內容 token 總數: {total_tokens}")
    print(f"估計成本 (GPT-4): ${total_tokens * 0.00003:.4f}")
    return total_tokens
```

---

## 權衡分析

| 優化方案 | Token 節省 | 精確度影響 | 實施難度 |
|---------|-----------|-----------|---------|
| 降低 parent chunk | 50% | 低 | 簡單 ⭐ |
| 降低 top_k | 40% | 中 | 簡單 ⭐ |
| 內容截斷 | 30% | 中 | 中等 ⭐⭐ |
| 兩階段檢索 | 70% | 低 | 困難 ⭐⭐⭐ |
| 動態預算 | 50% | 低 | 中等 ⭐⭐ |

---

## 測試建議

優化後務必測試:
1. 使用 `python -m rag_system.cli retrieve` 測試查詢品質
2. 比對優化前後的答案準確度
3. 測試邊緣案例 (很長/很短的問題)

---

**建議**: 先採用**平衡優化**配置,測試後再根據需求調整。

**Last Updated**: 2025-12-04
