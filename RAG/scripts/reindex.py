"""
reindex.py — 知識庫索引工具

用法：
  # 全量重建（清空後重建所有 .md 文件）
  python scripts/reindex.py

  # 單檔新增 / 更新（不清空其他文件）
  python scripts/reindex.py --file data/converted_md/陸海空軍懲罰法.md

  # 刪除指定文件的索引
  python scripts/reindex.py --delete 陸海空軍懲罰法.md
"""
import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Setup paths — assume script is in scripts/ and repo root is one level up
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from rag_system.core.config import RAGConfig
from rag_system.core.retrieval_generation import bump_retrieval_generation
from rag_system.services.ingestion import IngestionService


def build_config() -> RAGConfig:
    """Load and validate configuration from environment."""
    # .env 放在 ISO42001RAG 專案根目錄，即 repo_root 的上一層
    dotenv_path = repo_root.parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=True)
    try:
        config = RAGConfig.from_env()
        config.docstore_path = repo_root / "data/processed/docstore"
        config.validate()
        return config
    except Exception as e:
        print(f"[ERROR] 設定載入失敗: {e}")
        sys.exit(1)


def cmd_full_reindex(service: IngestionService, data_dir: Path) -> None:
    """全量重建：清空索引後重新掃描目錄。"""
    if not data_dir.exists():
        print(f"[ERROR] 資料目錄不存在: {data_dir}")
        sys.exit(1)

    print(f"📂 掃描目錄：{data_dir}")
    print("⚠️  清空現有索引...")
    service.clear_index()

    # Re-init to ensure fresh DB connection after drop/recreate
    config = service.config
    service = IngestionService(config)

    results = service.index_directory(data_dir, pattern="*.md")

    print("\n📊 索引摘要：")
    print(f"   ✅ 成功: {results['success']} 個文件")
    print(f"   ❌ 失敗: {results['failed']} 個文件")


def cmd_index_file(service: IngestionService, file_path: Path) -> None:
    """單檔新增 / 更新：先刪除舊索引再重新建立。"""
    if not file_path.exists():
        print(f"[ERROR] 找不到檔案: {file_path}")
        sys.exit(1)

    filename = file_path.name
    print(f"🔄 更新單檔索引：{filename}")

    # Delete existing index for this file first
    try:
        deleted = service.delete_document(filename)
        if deleted:
            print(f"   🗑️  已刪除舊索引（{deleted} 個 chunks）")
    except Exception as e:
        print(f"   ⚠️  刪除舊索引時發生警告（繼續執行）: {e}")

    # Index the file
    try:
        service.index_file(file_path)
        print(f"   ✅ 索引完成：{filename}")
    except Exception as e:
        print(f"   ❌ 索引失敗: {e}")
        sys.exit(1)


def cmd_delete(service: IngestionService, filename: str) -> None:
    """刪除指定文件的索引。"""
    print(f"🗑️  刪除索引：{filename}")
    try:
        deleted = service.delete_document(filename)
        if deleted:
            print(f"   ✅ 已刪除 {deleted} 個 chunks")
        else:
            print(f"   ℹ️  索引中找不到 {filename}，無需刪除")
    except Exception as e:
        print(f"   ❌ 刪除失敗: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="知識庫索引工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  全量重建（清空後掃描所有文件）：
    python scripts/reindex.py

  新增 / 更新單一文件：
    python scripts/reindex.py --file data/converted_md/陸海空軍懲罰法.md
    python scripts/reindex.py --file /絕對路徑/某法規.md

  刪除指定文件的索引：
    python scripts/reindex.py --delete 陸海空軍懲罰法.md
        """
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--file", "-f",
        metavar="FILE",
        help="指定單一 .md 檔案路徑，更新此文件的索引（不影響其他文件）",
    )
    group.add_argument(
        "--delete", "-d",
        metavar="FILENAME",
        help="刪除指定檔名的索引（例如：陸海空軍懲罰法.md）",
    )

    args = parser.parse_args()

    # Build config & service
    config = build_config()
    print("🚀 初始化 Ingestion Service...")
    service = IngestionService(config)

    if args.file:
        # Single-file update mode
        file_path = Path(args.file)
        if not file_path.is_absolute():
            # Resolve relative to repo root
            file_path = repo_root / file_path
        cmd_index_file(service, file_path)

    elif args.delete:
        # Delete mode
        cmd_delete(service, args.delete)

    else:
        # Full reindex mode (default)
        data_dir = repo_root / "data/converted_md"
        cmd_full_reindex(service, data_dir)

    generation = bump_retrieval_generation()
    print(f"🔄 Retrieval cache generation 已更新：{generation[:12]}")


if __name__ == "__main__":
    main()
