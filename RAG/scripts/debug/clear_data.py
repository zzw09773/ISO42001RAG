#!/usr/bin/env python3
"""
清空資料庫資料但保留結構

使用方式:
    python scripts/clear_data.py

或在 Docker 容器中:
    docker exec rag_jupyter python /home/jovyan/work/scripts/clear_data.py
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


def check_table_exists(conn, table_name: str) -> bool:
    """檢查資料表是否存在"""
    result = conn.execute(text("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = :table_name
        )
    """), {"table_name": table_name})
    return result.scalar()


def get_table_count(conn, table_name: str) -> int:
    """取得資料表筆數"""
    try:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        return result.scalar()
    except Exception:
        return 0


def clear_database(dry_run: bool = False):
    """
    清空所有資料表但保留結構

    Args:
        dry_run: 如果為 True，只顯示將要執行的操作，不實際執行
    """
    conn_string = os.getenv('PGVECTOR_URL')
    if not conn_string:
        raise ValueError("PGVECTOR_URL not found in environment")

    print(f"連接字串: {conn_string}")
    engine = create_engine(conn_string)

    # 要清空的資料表（當前架構使用 ParentDocumentRetriever）
    tables = [
        'langchain_pg_embedding',      # 子區塊向量（Child Chunks）
        'langchain_pg_collection',     # Collection 管理
    ]

    with engine.connect() as conn:
        print("\n" + "=" * 60)
        print("資料庫清空作業")
        print("=" * 60)

        # 顯示當前狀態
        print("\n【當前狀態】")
        total_records = 0
        for table in tables:
            if check_table_exists(conn, table):
                count = get_table_count(conn, table)
                total_records += count
                print(f"  {table:40s}: {count:6d} 筆")
            else:
                print(f"  {table:40s}: (不存在)")

        print(f"\n總計: {total_records} 筆資料")

        if total_records == 0:
            print("\n資料庫已經是空的，無需清空。")
            return

        if dry_run:
            print("\n【模擬模式】以下是將要執行的操作：")
            for table in tables:
                if check_table_exists(conn, table):
                    count = get_table_count(conn, table)
                    if count > 0:
                        print(f"  - TRUNCATE TABLE {table} CASCADE  (將刪除 {count} 筆)")
            print("\n使用 --execute 參數實際執行清空操作")
            return

        # 實際清空
        print("\n【執行清空】")
        success_count = 0
        failed_count = 0

        for table in tables:
            if not check_table_exists(conn, table):
                print(f"⊘ 跳過: {table} (資料表不存在)")
                continue

            try:
                count_before = get_table_count(conn, table)
                if count_before == 0:
                    print(f"○ 跳過: {table} (已經是空的)")
                    continue

                conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
                conn.commit()
                print(f"✓ 已清空: {table} (刪除了 {count_before} 筆)")
                success_count += 1
            except Exception as e:
                print(f"✗ 失敗: {table} - {e}")
                failed_count += 1
                conn.rollback()

        # 顯示結果
        print("\n" + "=" * 60)
        print("清空作業完成")
        print("=" * 60)
        print(f"成功: {success_count} 個資料表")
        print(f"失敗: {failed_count} 個資料表")

        # 驗證結果
        print("\n【清空後狀態】")
        for table in tables:
            if check_table_exists(conn, table):
                count = get_table_count(conn, table)
                symbol = "✓" if count == 0 else "✗"
                print(f"  {symbol} {table:40s}: {count:6d} 筆")


def clear_docstore(dry_run: bool = False):
    """清空本地 docstore 快取"""
    docstore_path = project_root / ".storage" / "docstore"

    if not docstore_path.exists():
        print("\nDocstore 目錄不存在，無需清空。")
        return

    files = list(docstore_path.glob("*"))
    if not files:
        print("\nDocstore 已經是空的。")
        return

    print(f"\n【Docstore 清空】")
    print(f"路徑: {docstore_path}")
    print(f"檔案數量: {len(files)}")

    if dry_run:
        print("【模擬模式】將刪除以下檔案：")
        for f in files[:10]:  # 只顯示前 10 個
            print(f"  - {f.name}")
        if len(files) > 10:
            print(f"  ... 以及其他 {len(files) - 10} 個檔案")
        return

    import shutil
    try:
        shutil.rmtree(docstore_path)
        docstore_path.mkdir(parents=True)
        print(f"✓ 已清空 docstore ({len(files)} 個檔案)")
    except Exception as e:
        print(f"✗ 清空 docstore 失敗: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="清空 RAG 系統資料庫資料（保留結構）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  # 模擬模式（預設）- 只顯示將要執行的操作
  python scripts/clear_data.py

  # 實際執行清空
  python scripts/clear_data.py --execute

  # 同時清空 docstore
  python scripts/clear_data.py --execute --docstore

  # 只清空 docstore
  python scripts/clear_data.py --execute --docstore-only
        """
    )

    parser.add_argument(
        '--execute',
        action='store_true',
        help='實際執行清空操作（預設為模擬模式）'
    )

    parser.add_argument(
        '--docstore',
        action='store_true',
        help='同時清空本地 docstore 快取'
    )

    parser.add_argument(
        '--docstore-only',
        action='store_true',
        help='只清空 docstore，不清空資料庫'
    )

    parser.add_argument(
        '--yes',
        action='store_true',
        help='跳過確認提示'
    )

    args = parser.parse_args()

    dry_run = not args.execute

    if dry_run:
        print("\n⚠️  模擬模式 - 不會實際執行任何操作")
        print("使用 --execute 參數來實際執行清空\n")

    try:
        if args.docstore_only:
            # 只清空 docstore
            if not dry_run and not args.yes:
                confirm = input("\n確定要清空 docstore？(yes/no): ")
                if confirm.lower() != 'yes':
                    print("已取消操作")
                    return
            clear_docstore(dry_run)
        else:
            # 清空資料庫
            if not dry_run and not args.yes:
                confirm = input("\n⚠️  確定要清空資料庫所有資料？此操作無法復原！(yes/no): ")
                if confirm.lower() != 'yes':
                    print("已取消操作")
                    return

            clear_database(dry_run)

            # 如果指定，也清空 docstore
            if args.docstore:
                clear_docstore(dry_run)

        if not dry_run:
            print("\n✓ 作業完成！")
            print("\n下一步：重新建立索引")
            print("  - 使用 Notebook: notebooks/1_build_index.ipynb")
            print("  - 使用腳本: python reindex_script.py")

    except Exception as e:
        print(f"\n✗ 錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
