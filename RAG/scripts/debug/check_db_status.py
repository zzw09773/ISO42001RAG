#!/usr/bin/env python3
"""
檢查資料庫狀態

使用方式:
    python scripts/check_db_status.py

或在 Docker 容器中:
    docker exec rag_jupyter python /home/jovyan/work/scripts/check_db_status.py
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# 確保能導入專案模組
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

load_dotenv()


def format_size(size_bytes: int) -> str:
    """格式化檔案大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def check_database_status():
    """檢查資料庫狀態"""
    conn_string = os.getenv('PGVECTOR_URL')
    if not conn_string:
        raise ValueError("PGVECTOR_URL not found in environment")

    print("=" * 70)
    print("RAG 資料庫狀態檢查")
    print("=" * 70)
    print(f"\n連接字串: {conn_string}\n")

    engine = create_engine(conn_string)

    with engine.connect() as conn:
        # 1. 檢查資料表
        print("【資料表列表】")
        result = conn.execute(text("""
            SELECT
                tablename,
                pg_size_pretty(pg_total_relation_size('public.' || tablename)) AS size
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """))

        tables = []
        for row in result:
            tables.append(row[0])
            print(f"  ✓ {row[0]:40s} {row[1]:>15s}")

        # 2. 統計各資料表筆數
        print("\n【資料統計】")

        table_stats = [
            ('langchain_pg_embedding', '子區塊向量 (Child Chunks)'),
            ('langchain_pg_collection', 'Collection'),
        ]

        total_count = 0
        for table_name, description in table_stats:
            if table_name in tables:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count = result.scalar()
                total_count += count
                print(f"  {description:20s}: {count:8d} 筆")
            else:
                print(f"  {description:20s}: (不存在)")

        print(f"\n  總計: {total_count:8d} 筆")

        # 3. 檢查 Collections
        if 'langchain_pg_collection' in tables:
            print("\n【Collections】")
            result = conn.execute(text("""
                SELECT
                    c.name,
                    c.uuid,
                    COUNT(e.id) as embedding_count
                FROM langchain_pg_collection c
                LEFT JOIN langchain_pg_embedding e ON c.uuid = e.collection_id
                GROUP BY c.name, c.uuid
                ORDER BY c.name
            """))

            collections = list(result)
            if collections:
                for row in collections:
                    print(f"  • {row[0]:30s} (UUID: {row[1]}, 向量數: {row[2]})")
            else:
                print("  (無 Collection)")

        # 4. 檢查向量內容範例
        if 'langchain_pg_embedding' in tables:
            result = conn.execute(text("SELECT COUNT(*) FROM langchain_pg_embedding"))
            embed_count = result.scalar()

            if embed_count > 0:
                print("\n【向量資料範例】(前 3 筆)")
                result = conn.execute(text("""
                    SELECT
                        id,
                        substring(document, 1, 60) as doc_preview,
                        cmetadata
                    FROM langchain_pg_embedding
                    ORDER BY id DESC
                    LIMIT 3
                """))

                for i, row in enumerate(result, 1):
                    print(f"  {i}. ID: {row[0]}")
                    print(f"     內容: {row[1]}...")
                    print(f"     Metadata: {row[2]}")
            else:
                print("\n【向量資料】")
                print("  (尚未索引任何文件)")

        # 5. 資料庫大小
        print("\n【資料庫大小】")
        result = conn.execute(text("""
            SELECT pg_database_size(current_database())
        """))
        db_size = result.scalar()
        print(f"  資料庫總大小: {format_size(db_size)}")

        # 6. 檢查擴充功能
        print("\n【擴充功能】")
        result = conn.execute(text("""
            SELECT extname, extversion
            FROM pg_extension
            WHERE extname IN ('vector', 'plpgsql')
        """))
        for row in result:
            print(f"  ✓ {row[0]:20s} v{row[1]}")

    print("\n" + "=" * 70)


def check_docstore_status():
    """檢查本地 docstore 狀態"""
    docstore_path = project_root / ".storage" / "docstore"

    print("\n【Docstore 狀態】")
    print(f"路徑: {docstore_path}")

    if not docstore_path.exists():
        print("  狀態: 目錄不存在")
        return

    files = list(docstore_path.glob("*"))
    if not files:
        print("  狀態: 空的")
        return

    total_size = sum(f.stat().st_size for f in files if f.is_file())
    print(f"  檔案數量: {len(files)}")
    print(f"  總大小: {format_size(total_size)}")

    # 顯示最近的檔案
    recent_files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
    if recent_files:
        print("\n  最近的檔案:")
        for f in recent_files:
            size = format_size(f.stat().st_size)
            print(f"    - {f.name:40s} {size:>15s}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="檢查 RAG 系統資料庫狀態")
    parser.add_argument(
        '--docstore',
        action='store_true',
        help='同時檢查本地 docstore 狀態'
    )

    args = parser.parse_args()

    try:
        check_database_status()

        if args.docstore:
            check_docstore_status()

        print("\n✓ 檢查完成！\n")

    except Exception as e:
        print(f"\n✗ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
