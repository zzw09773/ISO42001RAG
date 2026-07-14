#!/usr/bin/env bash
# Capture or compare non-secret runtime metrics before/after migration.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

MODE=print
TARGET=""
HELPER_IMAGE="nginx:alpine"
OPENWEBUI_IMAGE="ghcr.io/open-webui/open-webui:0.7.2"

usage() {
    cat <<'EOF'
用法：
  ./verify_runtime_migration.sh
  ./verify_runtime_migration.sh --write-snapshot FILE
  ./verify_runtime_migration.sh --compare FILE
  ./verify_runtime_migration.sh --compare-exact FILE

不讀取對話或帳號內容，只記錄資料筆數、檔案數與稽核日誌摘要。
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --write-snapshot)
            [[ $# -ge 2 ]] || { printf '缺少 snapshot 路徑\n' >&2; exit 2; }
            MODE=write
            TARGET="$2"
            shift 2
            ;;
        --compare)
            [[ $# -ge 2 ]] || { printf '缺少 snapshot 路徑\n' >&2; exit 2; }
            MODE=compare
            TARGET="$2"
            shift 2
            ;;
        --compare-exact)
            [[ $# -ge 2 ]] || { printf '缺少 snapshot 路徑\n' >&2; exit 2; }
            MODE=compare_exact
            TARGET="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf '未知選項：%s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for command in docker python3; do
    command -v "$command" >/dev/null 2>&1 || {
        printf '缺少必要指令：%s\n' "$command" >&2
        exit 1
    }
done
[[ "$MODE" != compare && "$MODE" != compare_exact || -f "$TARGET" ]] || {
    printf '找不到來源 snapshot：%s\n' "$TARGET" >&2
    exit 1
}
docker image inspect "$HELPER_IMAGE" >/dev/null 2>&1 || {
    printf '缺少 helper image：%s\n' "$HELPER_IMAGE" >&2
    exit 1
}
docker image inspect "$OPENWEBUI_IMAGE" >/dev/null 2>&1 || {
    printf '缺少 OpenWebUI image：%s\n' "$OPENWEBUI_IMAGE" >&2
    exit 1
}
for volume in iso42001rag_openwebui_data iso42001rag_keycloak_data; do
    docker volume inspect "$volume" >/dev/null 2>&1 || {
        printf '找不到 runtime volume：%s\n' "$volume" >&2
        exit 1
    }
done

pg_count() {
    local table="$1"
    docker exec ISO42001_pgvector sh -c \
        "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -Atc 'SELECT COUNT(*) FROM ${table};'" \
        2>/dev/null
}

openwebui_count() {
    local table="$1"
    docker run --rm -i --network none --entrypoint python \
        -v iso42001rag_openwebui_data:/data:ro \
        "$OPENWEBUI_IMAGE" - "$table" <<'PY'
import sqlite3
import sys

table = sys.argv[1]
allowed = {"user", "chat", "message"}
if table not in allowed:
    raise SystemExit("unsupported table")
con = sqlite3.connect("file:/data/webui.db?mode=ro", uri=True)
exists = con.execute(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
).fetchone()
print(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if exists else 0)
PY
}

volume_metrics() {
    local volume="$1"
    docker run --rm -i --network none --entrypoint python \
        -v "${volume}:/data:ro" "$OPENWEBUI_IMAGE" - <<'PY'
import hashlib
from pathlib import Path

root = Path("/data")
paths = sorted(path for path in root.rglob("*") if path.is_file())
digest = hashlib.sha256()
total = 0
for path in paths:
    relative = path.relative_to(root).as_posix().encode("utf-8", "surrogateescape")
    digest.update(relative)
    digest.update(b"\0")
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            digest.update(chunk)
print(len(paths), total, digest.hexdigest() if paths else "empty")
PY
}

bind_file_count() {
    local relative="$1"
    docker run --rm --network none --entrypoint sh \
        -v "${ROOT_DIR}:/project:ro" "$HELPER_IMAGE" -c \
        'find "/project/$1" -type f 2>/dev/null | wc -l' _ "$relative"
}

audit_metrics() {
    docker run --rm --network none --entrypoint sh \
        -v "${ROOT_DIR}/RAG/data:/data:ro" "$HELPER_IMAGE" -c '
files=$(find /data/audit_logs -type f -name "*.jsonl" 2>/dev/null | sort)
if [ -n "$files" ]; then
    count=$(printf "%s\n" "$files" | wc -l)
    lines=$(printf "%s\n" "$files" | xargs cat | wc -l)
    digest=$(printf "%s\n" "$files" | xargs sha256sum | sha256sum | awk "{print \$1}")
else
    count=0
    lines=0
    digest=empty
fi
printf "%s %s %s\n" "$count" "$lines" "$digest"
'
}

docker inspect ISO42001_pgvector --format '{{.State.Running}}' 2>/dev/null | grep -qx true || {
    printf 'PostgreSQL 容器未運行，無法建立完整摘要。\n' >&2
    exit 1
}

conversations="$(pg_count conversations)"
collections="$(pg_count langchain_pg_collection)"
embeddings="$(pg_count langchain_pg_embedding)"
ow_users="$(openwebui_count user)"
ow_chats="$(openwebui_count chat)"
ow_messages="$(openwebui_count message)"
read -r keycloak_files keycloak_bytes keycloak_digest < <(volume_metrics iso42001rag_keycloak_data)
read -r openwebui_files openwebui_bytes openwebui_digest < <(volume_metrics iso42001rag_openwebui_data)
read -r audit_files audit_lines audit_digest < <(audit_metrics)
monitoring_files="$(bind_file_count monitoring_addon/data)"
admin_files="$(bind_file_count admin_console/data)"

temp_snapshot="$(mktemp "${TMPDIR:-/tmp}/iso42001-runtime-snapshot.XXXXXX")"
trap 'rm -f "$temp_snapshot"' EXIT
python3 - "$temp_snapshot" \
    "$conversations" "$collections" "$embeddings" \
    "$ow_users" "$ow_chats" "$ow_messages" \
    "$keycloak_files" "$keycloak_bytes" "$keycloak_digest" \
    "$openwebui_files" "$openwebui_bytes" "$openwebui_digest" \
    "$audit_files" "$audit_lines" "$audit_digest" "$monitoring_files" "$admin_files" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

names = (
    "postgres_conversations", "postgres_collections", "postgres_embeddings",
    "openwebui_users", "openwebui_chats", "openwebui_messages",
    "keycloak_files", "keycloak_bytes", "keycloak_digest",
    "openwebui_files", "openwebui_bytes", "openwebui_digest",
    "audit_files", "audit_lines", "audit_digest", "monitoring_files", "admin_files",
)
values = sys.argv[2:]
metrics = {}
for name, value in zip(names, values):
    metrics[name] = value if name.endswith("_digest") else int(value)
payload = {
    "schema_version": 1,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "compose_project": "iso42001rag",
    "metrics": metrics,
}
Path(sys.argv[1]).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

case "$MODE" in
    print)
        cat "$temp_snapshot"
        ;;
    write)
        mkdir -p "$(dirname "$TARGET")"
        cp "$temp_snapshot" "$TARGET"
        chmod 600 "$TARGET"
        printf '已寫入 runtime snapshot：%s\n' "$TARGET"
        ;;
    compare|compare_exact)
        python3 - "$TARGET" "$temp_snapshot" "$MODE" <<'PY'
import json
import sys

expected = json.load(open(sys.argv[1], encoding="utf-8"))
current = json.load(open(sys.argv[2], encoding="utf-8"))
mode = sys.argv[3]
if expected.get("schema_version") != 1 or current.get("schema_version") != 1:
    raise SystemExit("不支援的 snapshot schema")
e = expected["metrics"]
c = current["metrics"]
minimum_keys = (
    "postgres_conversations", "postgres_collections", "postgres_embeddings",
    "openwebui_users", "openwebui_chats", "openwebui_messages",
    "audit_files", "audit_lines", "monitoring_files", "admin_files",
)
failures = []
for key in minimum_keys:
    if c[key] < e[key]:
        failures.append(f"{key}: expected >= {e[key]}, current {c[key]}")
if c["audit_lines"] == e["audit_lines"] and c["audit_digest"] != e["audit_digest"]:
    failures.append("audit_digest: 行數相同但內容摘要不一致")
for key in ("keycloak_files", "keycloak_bytes", "openwebui_files", "openwebui_bytes"):
    if c[key] <= 0:
        failures.append(f"{key}: restored data is empty")
if mode == "compare_exact":
    exact_keys = minimum_keys + (
        "keycloak_files", "keycloak_bytes", "keycloak_digest",
        "openwebui_files", "openwebui_bytes", "openwebui_digest",
        "audit_digest",
    )
    for key in exact_keys:
        if c[key] != e[key]:
            failures.append(f"{key}: expected exact {e[key]}, current {c[key]}")
print("來源 → 目前 runtime 指標")
for key in minimum_keys:
    print(f"  {key}: {e[key]} -> {c[key]}")
if failures:
    print("遷移驗證失敗：", file=sys.stderr)
    for item in failures:
        print(f"  - {item}", file=sys.stderr)
    raise SystemExit(1)
print("遷移冷資料精確驗證：OK" if mode == "compare_exact" else "遷移資料數量與稽核摘要驗證：OK")
PY
        ;;
esac
