"""
Version Tracker — ISO 42001 A.6 / A.9 變更管理

在沒有 Git 的內網環境中，提供：
  snapshot  — 計算所有原始碼 SHA-256 並存檔，可選 tar 備份
  diff      — 比對當前檔案與上次快照
  verify    — 驗證當前檔案完整性是否與指定快照一致
  changelog — 自動將變更資訊追加至 CHANGELOG.md

Usage:
    python3 scripts/version_tracker.py snapshot --message "新增安全模組"
    python3 scripts/version_tracker.py snapshot --message "v1.1.0 release" --backup
    python3 scripts/version_tracker.py diff
    python3 scripts/version_tracker.py diff --base data/versions/snapshot_2026-04-09_120000.json
    python3 scripts/version_tracker.py verify --base data/versions/snapshot_2026-04-09_120000.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = PROJECT_ROOT / "data" / "versions"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"

# Only track these extensions (source code + config + docs)
TRACKED_EXTENSIONS = {
    ".py", ".md", ".json", ".yaml", ".yml", ".txt", ".sh",
    ".toml", ".cfg", ".ini", ".env.example",
    ".html", ".css", ".js",
}

# Directories to skip
SKIP_DIRS = {
    "__pycache__", ".git", ".ipynb_checkpoints", "node_modules",
    ".pytest_cache", "venv", ".venv", ".mypy_cache",
}

# Files/dirs under data/ to skip (logs, reports, versions are runtime artifacts)
SKIP_DATA_SUBDIRS = {"audit_logs", "reports", "versions", "output", "processed"}


# ---------------------------------------------------------------------------
# Core: file hashing
# ---------------------------------------------------------------------------

def _should_track(path: Path) -> bool:
    """Return True if this file should be tracked for version control."""
    for part in path.parts:
        if part in SKIP_DIRS:
            return False
    # Skip runtime data subdirectories
    try:
        rel = path.relative_to(PROJECT_ROOT / "data")
        if rel.parts and rel.parts[0] in SKIP_DATA_SUBDIRS:
            return False
    except ValueError:
        pass
    return path.suffix in TRACKED_EXTENSIONS or path.name in {
        "Dockerfile", "Dockerfile.api", ".gitignore", ".env.example",
    }


def _sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_snapshot(root: Path = PROJECT_ROOT) -> Dict[str, str]:
    """Walk project tree and return {relative_path: sha256_hash}."""
    snapshot: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if _should_track(fpath):
                rel = str(fpath.relative_to(root))
                snapshot[rel] = _sha256(fpath)
    return snapshot


# ---------------------------------------------------------------------------
# Snapshot management
# ---------------------------------------------------------------------------

def _latest_snapshot() -> Optional[Path]:
    """Find the most recent snapshot file."""
    if not VERSIONS_DIR.exists():
        return None
    snapshots = sorted(VERSIONS_DIR.glob("snapshot_*.json"))
    return snapshots[-1] if snapshots else None


def _load_snapshot(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _timestamp_str() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def save_snapshot(
    message: str = "",
    operator: str = "",
    version: str = "",
    create_backup: bool = False,
) -> Path:
    """Compute and save a new snapshot. Optionally create a tar.gz backup."""
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    ts = _timestamp_str()
    hashes = compute_snapshot()

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "local_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": message,
        "operator": operator,
        "version": version,
        "file_count": len(hashes),
        "files": hashes,
    }

    snap_path = VERSIONS_DIR / f"snapshot_{ts}.json"
    snap_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if create_backup:
        backup_path = VERSIONS_DIR / f"backup_{ts}.tar.gz"
        # Build file list (tracked files only)
        file_list = list(hashes.keys())
        list_file = VERSIONS_DIR / f"_filelist_{ts}.txt"
        list_file.write_text("\n".join(file_list), encoding="utf-8")
        try:
            subprocess.run(
                ["tar", "czf", str(backup_path), "-C", str(PROJECT_ROOT),
                 "-T", str(list_file)],
                check=True, capture_output=True,
            )
            list_file.unlink()
            print(f"備份：{backup_path}（{backup_path.stat().st_size / 1024:.0f} KB）")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"⚠️  備份失敗（tar 指令錯誤）：{e}")

    return snap_path


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def compute_diff(
    base: Optional[Path] = None,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Compare current files against a base snapshot.

    Returns (added, modified, deleted) file lists.
    """
    if base is None:
        base = _latest_snapshot()
    if base is None:
        return [], [], []

    old_data = _load_snapshot(base)
    old_files: Dict[str, str] = old_data.get("files", {})
    new_files = compute_snapshot()

    old_set = set(old_files.keys())
    new_set = set(new_files.keys())

    added = sorted(new_set - old_set)
    deleted = sorted(old_set - new_set)
    modified = sorted(
        f for f in (old_set & new_set)
        if old_files[f] != new_files[f]
    )

    return added, modified, deleted


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify_integrity(base: Optional[Path] = None) -> Tuple[bool, List[str]]:
    """
    Verify current file hashes against a snapshot.

    Returns (all_ok, list_of_mismatches).
    """
    if base is None:
        base = _latest_snapshot()
    if base is None:
        return False, ["找不到任何快照檔案"]

    old_data = _load_snapshot(base)
    old_files: Dict[str, str] = old_data.get("files", {})
    issues: List[str] = []

    for rel_path, expected_hash in sorted(old_files.items()):
        full_path = PROJECT_ROOT / rel_path
        if not full_path.exists():
            issues.append(f"❌ 檔案遺失：{rel_path}")
            continue
        actual_hash = _sha256(full_path)
        if actual_hash != expected_hash:
            issues.append(f"⚠️  雜湊不符：{rel_path}")

    return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------

def append_changelog(
    message: str,
    version: str,
    operator: str,
    added: List[str],
    modified: List[str],
    deleted: List[str],
) -> None:
    """Append a change entry to CHANGELOG.md."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"\n## [{version}] - {now}",
        f"**操作者**：{operator or '（未填寫）'}  ",
        f"**說明**：{message or '（無）'}  ",
        f"**審核簽名**：＿＿＿＿＿＿＿＿  ",
        "",
    ]

    if added:
        lines.append("### 新增檔案")
        for f in added:
            lines.append(f"- `{f}`")
        lines.append("")

    if modified:
        lines.append("### 修改檔案")
        for f in modified:
            lines.append(f"- `{f}`")
        lines.append("")

    if deleted:
        lines.append("### 刪除檔案")
        for f in deleted:
            lines.append(f"- `{f}`")
        lines.append("")

    lines.append("---\n")

    # Create CHANGELOG if it doesn't exist
    if not CHANGELOG_PATH.exists():
        header = "# 變更紀錄（Change Log）\n\n"
        header += "*本紀錄由 `scripts/version_tracker.py` 自動產生，作為 ISO 42001 A.6/A.9 稽核證據。*\n"
        header += "*每筆紀錄的「審核簽名」欄位由審核人員手動填寫。*\n\n---\n"
        CHANGELOG_PATH.write_text(header, encoding="utf-8")

    with open(CHANGELOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_snapshot(args):
    # Step 1: compute diff against previous snapshot (if any)
    added, modified, deleted = compute_diff()
    total_changes = len(added) + len(modified) + len(deleted)

    # Step 2: write changelog BEFORE hashing — CHANGELOG.md is a tracked file;
    # hashing must happen after it is updated so the snapshot stays self-consistent.
    if total_changes > 0:
        append_changelog(
            message=args.message,
            version=args.version,
            operator=args.operator,
            added=added,
            modified=modified,
            deleted=deleted,
        )
        print(f"變更紀錄已追加至：{CHANGELOG_PATH}")
    else:
        print("無變更，CHANGELOG 未更新。")

    # Step 3: hash all tracked files (including updated CHANGELOG.md) and save
    snap_path = save_snapshot(
        message=args.message,
        operator=args.operator,
        version=args.version,
        create_backup=args.backup,
    )
    print(f"快照已儲存：{snap_path}")
    print(f"追蹤檔案數：{len(compute_snapshot())}")
    print(f"變更統計：新增 {len(added)}、修改 {len(modified)}、刪除 {len(deleted)}")


def cmd_diff(args):
    base = Path(args.base) if args.base else None
    added, modified, deleted = compute_diff(base)

    if not (added or modified or deleted):
        print("✅ 與上次快照完全一致，無任何變更。")
        return

    if added:
        print(f"\n📁 新增（{len(added)} 檔）：")
        for f in added:
            print(f"  + {f}")
    if modified:
        print(f"\n✏️  修改（{len(modified)} 檔）：")
        for f in modified:
            print(f"  ~ {f}")
    if deleted:
        print(f"\n🗑️  刪除（{len(deleted)} 檔）：")
        for f in deleted:
            print(f"  - {f}")

    print(f"\n合計：+{len(added)} ~{len(modified)} -{len(deleted)}")


def cmd_verify(args):
    base = Path(args.base) if args.base else None
    ok, issues = verify_integrity(base)

    if ok:
        print("✅ 完整性驗證通過：所有檔案與快照一致。")
    else:
        print(f"❌ 完整性驗證失敗（{len(issues)} 個問題）：")
        for issue in issues:
            print(f"  {issue}")
    sys.exit(0 if ok else 1)


def cmd_list(_args):
    """List all snapshots."""
    if not VERSIONS_DIR.exists():
        print("尚無快照。")
        return
    snapshots = sorted(VERSIONS_DIR.glob("snapshot_*.json"))
    if not snapshots:
        print("尚無快照。")
        return
    print(f"共 {len(snapshots)} 個快照：\n")
    for sp in snapshots:
        data = _load_snapshot(sp)
        msg = data.get("message", "")
        ver = data.get("version", "")
        fc = data.get("file_count", "?")
        ts = data.get("local_time", sp.stem)
        label = f"[{ver}] " if ver else ""
        print(f"  {sp.name}  {label}{ts}  ({fc} 檔)  {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="ISO 42001 版本追蹤工具（無需 Git）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # snapshot
    p_snap = sub.add_parser("snapshot", help="建立新的版本快照")
    p_snap.add_argument("-m", "--message", default="", help="變更說明")
    p_snap.add_argument("-o", "--operator", default="", help="操作者姓名")
    p_snap.add_argument("-v", "--version", default="", help="版本號（如 v1.1.0）")
    p_snap.add_argument("--backup", action="store_true", help="同時建立 tar.gz 備份")
    p_snap.set_defaults(func=cmd_snapshot)

    # diff
    p_diff = sub.add_parser("diff", help="比對當前檔案與上次快照")
    p_diff.add_argument("--base", default=None, help="指定基準快照檔案路徑")
    p_diff.set_defaults(func=cmd_diff)

    # verify
    p_ver = sub.add_parser("verify", help="驗證檔案完整性")
    p_ver.add_argument("--base", default=None, help="指定基準快照檔案路徑")
    p_ver.set_defaults(func=cmd_verify)

    # list
    p_list = sub.add_parser("list", help="列出所有快照")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
