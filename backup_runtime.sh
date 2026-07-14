#!/usr/bin/env bash
# Back up every persistent runtime data surface needed for an ISO42001RAG migration.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
umask 077

OUTPUT_DIR=""
ASSUME_YES=false
LEAVE_STOPPED=false
HELPER_IMAGE="nginx:alpine"
MUTABLE_SERVICES=(openwebui rag-api keycloak jupyter monitoring admin)
ACTIVE_SERVICES=()

usage() {
    cat <<'EOF'
用法：./backup_runtime.sh [選項]

選項：
  --output DIR       備份輸出目錄（預設 runtime_backups/runtime-<時間>）
  --yes              不詢問確認
  --leave-stopped    完成後不重新啟動原本運行中的寫入服務
  -h, --help         顯示說明

備份內容：PostgreSQL、OpenWebUI、Keycloak volumes，
以及 RAG/monitoring/admin 執行期資料。目標機新版 .env/TLS 不在遷移範圍。
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            [[ $# -ge 2 ]] || { printf '缺少 --output 路徑\n' >&2; exit 2; }
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --yes)
            ASSUME_YES=true
            shift
            ;;
        --leave-stopped)
            LEAVE_STOPPED=true
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

for command in docker tar sha256sum python3; do
    command -v "$command" >/dev/null 2>&1 || {
        printf '缺少必要指令：%s\n' "$command" >&2
        exit 1
    }
done
[[ -f .env ]] || { printf '找不到 .env，拒絕建立不完整備份。\n' >&2; exit 1; }
docker image inspect "$HELPER_IMAGE" >/dev/null 2>&1 || {
    printf '缺少離線 helper image：%s\n' "$HELPER_IMAGE" >&2
    exit 1
}

if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="runtime_backups/runtime-$(date '+%Y%m%d-%H%M%S')"
fi
if [[ -e "$OUTPUT_DIR" ]] && [[ -n "$(find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    printf '輸出目錄已存在且非空：%s\n' "$OUTPUT_DIR" >&2
    exit 1
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
chmod 700 "$OUTPUT_DIR"

if [[ "$ASSUME_YES" != true ]]; then
    printf '將短暫停止 OpenWebUI、RAG、Keycloak、監控與管理服務以建立一致備份。\n'
    read -r -p '輸入 BACKUP 繼續：' answer
    [[ "$answer" == "BACKUP" ]] || { printf '已取消。\n'; exit 1; }
fi

mapfile -t running_services < <(docker compose ps --services --status running)
for wanted in "${MUTABLE_SERVICES[@]}"; do
    for running in "${running_services[@]}"; do
        if [[ "$wanted" == "$running" ]]; then
            ACTIVE_SERVICES+=("$wanted")
            break
        fi
    done
done
printf '%s\n' "${running_services[@]}" > "$OUTPUT_DIR/running-services.txt"

restart_services() {
    local status=$?
    trap - EXIT INT TERM
    if [[ "$LEAVE_STOPPED" != true && ${#ACTIVE_SERVICES[@]} -gt 0 ]]; then
        printf '重新啟動備份前原本運行的服務...\n'
        if ! docker compose start "${ACTIVE_SERVICES[@]}"; then
            printf '警告：部分服務未能自動重新啟動，請人工檢查 docker compose ps。\n' >&2
            status=1
        fi
    fi
    exit "$status"
}
trap restart_services EXIT INT TERM

if [[ ${#ACTIVE_SERVICES[@]} -gt 0 ]]; then
    printf '停止寫入服務：%s\n' "${ACTIVE_SERVICES[*]}"
    docker compose stop "${ACTIVE_SERVICES[@]}"
fi

docker inspect ISO42001_pgvector --format '{{.State.Running}}' 2>/dev/null | grep -qx true || {
    printf 'PostgreSQL 容器 ISO42001_pgvector 未運行。\n' >&2
    exit 1
}

for volume in iso42001rag_openwebui_data iso42001rag_keycloak_data; do
    docker volume inspect "$volume" >/dev/null 2>&1 || {
        printf '找不到必要 volume：%s\n' "$volume" >&2
        exit 1
    }
done

printf '建立遷移前資料摘要...\n'
./verify_runtime_migration.sh --write-snapshot "$OUTPUT_DIR/source-snapshot.json"

printf '匯出 PostgreSQL...\n'
docker exec ISO42001_pgvector sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    > "$OUTPUT_DIR/postgres.dump"

archive_volume() {
    local volume="$1"
    local archive="$2"
    printf '封存 volume：%s\n' "$volume"
    docker run --rm --network none --entrypoint sh \
        -v "${volume}:/source:ro" \
        -v "${OUTPUT_DIR}:/backup" \
        "$HELPER_IMAGE" -c \
        'cd /source && tar czf "/backup/$1" .' _ "$archive"
}

archive_volume iso42001rag_openwebui_data openwebui-data.tar.gz
archive_volume iso42001rag_keycloak_data keycloak-data.tar.gz
printf '封存 bind-mounted runtime 資料...\n'
docker run --rm --network none --entrypoint sh \
    -v "${ROOT_DIR}:/project:ro" \
    -v "${OUTPUT_DIR}:/backup" \
    "$HELPER_IMAGE" -c \
    'cd /project && tar czf /backup/bind-runtime.tar.gz RAG/data monitoring_addon/data admin_console/data'

cat > "$OUTPUT_DIR/backup-info.txt" <<EOF
format_version=1
created_at=$(date --iso-8601=seconds)
compose_project=iso42001rag
postgres_image=pgvector/pgvector:pg17
openwebui_image=ghcr.io/open-webui/open-webui:0.7.2
keycloak_image=quay.io/keycloak/keycloak:26.5.6
private_config_included=false
EOF

(
    cd "$OUTPUT_DIR"
    find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
        | sort -z \
        | xargs -0 sha256sum > SHA256SUMS
)
chmod 600 "$OUTPUT_DIR"/*

printf '\n備份完成：%s\n' "$OUTPUT_DIR"
printf '備份仍含帳號、對話與稽核資料，請使用核准的加密儲存媒體；傳輸前執行：\n'
printf '  (cd %q && sha256sum -c SHA256SUMS)\n' "$OUTPUT_DIR"
