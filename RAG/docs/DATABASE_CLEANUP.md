# 資料庫清空指南

本文件說明如何清空 RAG 系統的 PostgreSQL 資料庫資料。

---

## 📋 清空方式概覽

提供三種清空方式，依需求選擇：

1. **完全清空** - 刪除所有資料表（需重新建立 Schema）
2. **保留結構清空** - 只清空資料，保留資料表結構
3. **選擇性清空** - 只清空特定 Collection 或資料表

---

## 🔧 方法一：完全清空資料庫

### 適用情境
- 想要完全重置系統
- 需要修改資料表結構
- 開發測試時需要乾淨的環境

### 操作步驟

#### 1. 使用 Docker 進入資料庫
```bash
docker exec -it Judge_pgvector psql -U postgres -d Judge
```

#### 2. 刪除所有資料表
```sql
-- 查看所有資料表
\dt

-- 刪除所有公開 Schema 的資料表（包含 CASCADE）
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;

-- 重新啟用 pgvector 擴充
CREATE EXTENSION IF NOT EXISTS vector;

-- 退出
\q
```

#### 3. 重新建立索引
刪除後需重新執行索引腳本：
```bash
# 在 Docker 容器中
cd /home/jovyan/work
python scripts/reindex.py

# 或使用 Notebook
# 執行 notebooks/1_build_index.ipynb
```

---

## 🧹 方法二：保留結構清空資料

### 適用情境
- 只想清空資料，不想重建結構
- 快速重新索引

### 操作步驟

#### 1. 使用清空腳本（推薦）

使用 `scripts/debug/clear_data.py`：

```bash
# 在 Docker 容器中
python scripts/debug/clear_data.py --execute --docstore
```

#### 2. 或使用 SQL 直接清空
```bash
docker exec -it Judge_pgvector psql -U postgres -d Judge
```

```sql
-- 清空所有資料但保留結構
TRUNCATE TABLE rag_chunk_embeddings_detail CASCADE;
TRUNCATE TABLE rag_chunk_embeddings_summary CASCADE;
TRUNCATE TABLE rag_chunk_hierarchy CASCADE;
TRUNCATE TABLE rag_document_chunks CASCADE;
TRUNCATE TABLE rag_documents CASCADE;
TRUNCATE TABLE langchain_pg_embedding CASCADE;
TRUNCATE TABLE langchain_pg_collection CASCADE;

-- 確認資料已清空
SELECT COUNT(*) FROM langchain_pg_embedding;
SELECT COUNT(*) FROM rag_documents;
```

---

## 🎯 方法三：選擇性清空

### 適用情境
- 只想刪除特定 Collection 的資料
- 保留其他資料

### 清空特定 Collection

```sql
-- 連接資料庫
docker exec -it Judge_pgvector psql -U postgres -d Judge

-- 查看所有 Collection
SELECT uuid, name FROM langchain_pg_collection;

-- 刪除特定 Collection 的向量資料
-- 例如刪除 "laws" collection
DELETE FROM langchain_pg_embedding
WHERE collection_id = (
    SELECT uuid FROM langchain_pg_collection WHERE name = 'laws'
);

-- 刪除 Collection 本身
DELETE FROM langchain_pg_collection WHERE name = 'laws';
```

---

## 📊 檢查資料庫狀態

### 使用腳本檢查

```bash
python scripts/debug/check_db_status.py
```

---

## ⚠️ 注意事項

1. **備份重要資料**
   - 清空前請確認是否需要備份
   - 可使用 `pg_dump` 備份整個資料庫

2. **停止相關服務**
   - 清空前建議停止 API 服務和 Notebook kernel
   - 避免清空時有連線正在讀寫

3. **Document Store**
   - 如果使用了 `ParentDocumentRetriever` 的本地 docstore
   - 記得同時清空 `data/processed/docstore/` 目錄 (腳本會自動處理)

---

## 🔄 重新索引

清空後重新建立索引：

### 使用 Notebook（推薦）
```bash
# 開啟 Jupyter
# 訪問 http://localhost:25678
# 執行 notebooks/1_build_index.ipynb
```

### 使用腳本
```bash
# 在 Docker 容器中
cd /home/jovyan/work
python scripts/reindex.py
```

---

**Last Updated**: 2025-12-04
