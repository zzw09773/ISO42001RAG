#!/usr/bin/env bash
# Restore a backup produced by backup_runtime.sh onto a staged/fresh stack.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
umask 077

BACKUP_DIR=""
ASSUME_YES=false
NO_START=false
HELPER_IMAGE="nginx:alpine"

usage() {
    cat <<'EOF'
用法：./restore_runtime.sh --backup DIR [選項]

選項：
  --backup DIR              backup_runtime.sh 產生的完整備份目錄
  --no-start                還原後保持服務停止，不執行 deploy/驗證
  --yes                     不詢問破壞性操作確認
  -h, --help                顯示說明

警告：此工具會用來源備份「取代」目標 runtime，不會合併兩套歷史。
執行前會自動建立目標機 pre-restore 回退備份。
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup)
            [[ $# -ge 2 ]] || { printf '缺少 --backup 路徑\n' >&2; exit 2; }
            BACKUP_DIR="$2"
            shift 2
            ;;
        --no-start)
            NO_START=true
            shift
            ;;
        --yes)
            ASSUME_YES=true
            shift
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

[[ -n "$BACKUP_DIR" ]] || { usage >&2; exit 2; }
[[ -f .env ]] || {
    printf '目標機找不到新版 .env；請先建立並完成內網 IP／主機名稱設定。\n' >&2
    exit 1
}
[[ -d "$BACKUP_DIR" ]] || { printf '找不到備份目錄：%s\n' "$BACKUP_DIR" >&2; exit 1; }
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"

required_files=(
    SHA256SUMS backup-info.txt source-snapshot.json postgres.dump
    openwebui-data.tar.gz keycloak-data.tar.gz
    bind-runtime.tar.gz
)
for file in "${required_files[@]}"; do
    [[ -f "$BACKUP_DIR/$file" ]] || {
        printf '備份不完整，缺少：%s\n' "$file" >&2
        exit 1
    }
done

printf '驗證來源備份 SHA-256...\n'
(cd "$BACKUP_DIR" && sha256sum -c SHA256SUMS)

if [[ "$ASSUME_YES" != true ]]; then
    printf '\n此操作會取代目前 PostgreSQL、OpenWebUI、Keycloak 與 runtime 資料。\n'
    printf '來源備份：%s\n' "$BACKUP_DIR"
    read -r -p '輸入 RESTORE-RUNTIME 繼續：' answer
    [[ "$answer" == "RESTORE-RUNTIME" ]] || { printf '已取消。\n'; exit 1; }
fi

rollback_dir="runtime_backups/pre-restore-$(date '+%Y%m%d-%H%M%S')"
printf '先建立目標機回退備份：%s\n' "$rollback_dir"
./backup_runtime.sh --output "$rollback_dir" --yes --leave-stopped
rollback_dir="$(cd "$rollback_dir" && pwd)"

restore_volume() {
    local volume="$1"
    local archive="$2"
    printf '還原 volume：%s\n' "$volume"
    docker volume inspect "$volume" >/dev/null 2>&1 || docker volume create "$volume" >/dev/null
    docker run --rm --network none --entrypoint sh \
        -v "${volume}:/target" \
        -v "${BACKUP_DIR}:/backup:ro" \
        "$HELPER_IMAGE" -c \
        'find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar xzf "/backup/$1" -C /target' _ "$archive"
}

restore_volume iso42001rag_openwebui_data openwebui-data.tar.gz
restore_volume iso42001rag_keycloak_data keycloak-data.tar.gz
printf '還原 bind-mounted runtime 資料...\n'
docker run --rm --network none --entrypoint sh \
    -v "${ROOT_DIR}:/project" \
    -v "${BACKUP_DIR}:/backup:ro" \
    "$HELPER_IMAGE" -c '
set -eu
for path in RAG/data monitoring_addon/data admin_console/data; do
    mkdir -p "/project/$path"
    find "/project/$path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
done
tar xzf /backup/bind-runtime.tar.gz -C /project
'

printf '保留目標機新版 .env 與 TLS，不載入來源設定。\n'
printf '請確認內網 IP／主機名稱已調整，且新版 OAUTH_CLIENT_SECRET 與還原後 Keycloak client 一致。\n'

docker inspect ISO42001_pgvector --format '{{.State.Running}}' 2>/dev/null | grep -qx true || {
    printf '啟動 PostgreSQL 以還原 dump...\n'
    docker compose up -d --no-build --pull never db
}
printf '還原 PostgreSQL（清除目標 schema 後載入來源 dump）...\n'
docker exec -i ISO42001_pgvector sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges --exit-on-error' \
    < "$BACKUP_DIR/postgres.dump"

printf '在服務啟動前精確比對冷資料摘要...\n'
./verify_runtime_migration.sh --compare-exact "$BACKUP_DIR/source-snapshot.json"

printf '移除 OpenWebUI 舊持久化端點，讓新版 .env 的 IP／URL 在啟動時生效...\n'
docker run --rm -i --network none --entrypoint python \
    -v iso42001rag_openwebui_data:/data \
    ghcr.io/open-webui/open-webui:0.7.2 - <<'PY'
import json
import sqlite3

paths = (
    ("openai", "api_base_urls"),
    ("openai", "api_keys"),
    ("openai", "api_configs"),
    ("webui", "url"),
)
con = sqlite3.connect("/data/webui.db")
row = con.execute("SELECT id, data FROM config ORDER BY id LIMIT 1").fetchone()
if row is None:
    print("OpenWebUI config 尚未建立；啟動時將直接讀取新版環境設定。")
    raise SystemExit(0)
data = json.loads(row[1])
changed = []
for path in paths:
    parent = data
    for part in path[:-1]:
        parent = parent.get(part) if isinstance(parent, dict) else None
        if not isinstance(parent, dict):
            break
    if isinstance(parent, dict) and path[-1] in parent:
        parent.pop(path[-1])
        changed.append(".".join(path))
if changed:
    con.execute(
        "UPDATE config SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), row[0]),
    )
    con.commit()
    print("已清除舊持久化連線欄位：" + ", ".join(changed))
else:
    print("未發現舊持久化連線欄位。")
PY

if [[ "$NO_START" == true ]]; then
    printf '\n資料已還原，服務保持停止。回退備份：%s\n' "$rollback_dir"
    printf '確認設定後執行 ./deploy.sh，再執行：\n'
    printf '  ./verify_runtime_migration.sh --compare %q\n' "$BACKUP_DIR/source-snapshot.json"
    exit 0
fi

printf '以部署入口同步設定並啟動完整服務...\n'
./deploy.sh
printf '比對來源與還原後資料摘要...\n'
./verify_runtime_migration.sh --compare "$BACKUP_DIR/source-snapshot.json"

printf '\n歷史資料還原完成。回退備份保留於：%s\n' "$rollback_dir"
printf '仍須人工確認：Keycloak 舊帳號登入、OpenWebUI 舊對話、稽核鏈 valid=True。\n'
