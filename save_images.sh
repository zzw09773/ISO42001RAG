#!/bin/bash
# =============================================================
# ISO42001 系統 — 離線 Image 打包腳本
# 用法: ./save_images.sh [--use-local] [--output FILE.tar.gz]
#
# 範圍：完整內網 stack 所需 images
#   ✓ rag-api / embed-proxy / jupyter / monitoring / code-server / admin（本專案 build）
#   ✓ pgvector / openwebui:0.7.2 / keycloak:26.5.6 / nginx（Compose 核定 tag）
# 產出：images/*.tar、images/IMAGE_MANIFEST.txt，以及單一總包 .tar.gz + .sha256
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGES_DIR="$SCRIPT_DIR/images"
mkdir -p "$IMAGES_DIR"
BUNDLE_OUTPUT=""
USE_LOCAL=false

usage() {
    cat <<'EOF'
用法：./save_images.sh [選項]

選項：
  --use-local       使用目前已存在的核定 images，不執行 build／pull
  --output FILE     總包輸出路徑（預設 deploy_packages/iso42001rag-images-<時間>.tar.gz）
  -h, --help        顯示說明
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --use-local)
            USE_LOCAL=true
            shift
            ;;
        --output)
            [[ $# -ge 2 ]] || { echo "缺少 --output 路徑" >&2; exit 2; }
            BUNDLE_OUTPUT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知選項：$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

echo "=========================================="
echo "  離線 Image 打包（完整 ISO42001 stack）"
echo "=========================================="

# --- Step 1/2: optionally build/pull ---
if [[ "$USE_LOCAL" == true ]]; then
    echo ""
    echo "ℹ️  使用目前本機 images；略過 build/pull。"
else
    echo ""
    echo "⏳ 建置 rag-api、embed-proxy、jupyter、monitoring、code-server、admin images..."
    docker compose build rag-api embed-proxy jupyter monitoring code-server admin
    echo "✅ Build 完成"

    echo ""
    echo "⏳ 拉取 pgvector、openwebui、keycloak、nginx images..."
    docker compose pull db openwebui keycloak nginx
    echo "✅ Pull 完成"
fi

# --- Step 3: 匯出 images ---
echo ""
echo "⏳ 匯出 images..."

RAG_IMAGE="iso42001rag-rag-api:latest"
EMBED_PROXY_IMAGE="iso42001rag-embed-proxy:latest"
JUPYTER_IMAGE="iso42001rag-jupyter:latest"
MONITORING_IMAGE="iso42001rag-monitoring:latest"
CODE_SERVER_IMAGE="iso42001rag-code-server:latest"
ADMIN_IMAGE="iso42001rag-admin:latest"

IMAGE_SPECS=(
    "rag-api.tar|$RAG_IMAGE"
    "embed-proxy.tar|$EMBED_PROXY_IMAGE"
    "jupyter.tar|$JUPYTER_IMAGE"
    "monitoring.tar|$MONITORING_IMAGE"
    "code-server.tar|$CODE_SERVER_IMAGE"
    "admin.tar|$ADMIN_IMAGE"
    "pgvector.tar|pgvector/pgvector:pg17"
    "openwebui.tar|ghcr.io/open-webui/open-webui:0.7.2"
    "keycloak.tar|quay.io/keycloak/keycloak:26.5.6"
    "nginx.tar|nginx:alpine"
)

for spec in "${IMAGE_SPECS[@]}"; do
    image="${spec#*|}"
    docker image inspect "$image" >/dev/null 2>&1 || {
        echo "❌ 找不到 image：$image" >&2
        exit 1
    }
done

# RAG used to build from a broad `COPY . .` context. Refuse to package a stale
# local image if it still contains external-development runtime state or secrets.
echo "  🔍 確認 rag-api image 不含外網 runtime 資料／秘密檔..."
if ! docker run --rm --network none --entrypoint sh "$RAG_IMAGE" -c '
    test ! -e /app/data
    test -z "$(find /app -xdev -type f \( \
        -name ".env" -o -name ".env.*" -o \
        -name "*.key" -o -name "*.pem" -o -name "*.crt" -o \
        -name "*.p12" -o -name "*.pfx" \
    \) -print -quit)"
'; then
    echo "❌ rag-api image 含有 /app/data、環境檔或憑證，拒絕打包。" >&2
    echo "   請先用現行 RAG/.dockerignore 重建：docker compose build --no-cache rag-api" >&2
    exit 1
fi

echo "  📦 匯出 $RAG_IMAGE → images/rag-api.tar"
docker save -o "$IMAGES_DIR/rag-api.tar" "$RAG_IMAGE"

echo "  📦 匯出 $EMBED_PROXY_IMAGE → images/embed-proxy.tar"
docker save -o "$IMAGES_DIR/embed-proxy.tar" "$EMBED_PROXY_IMAGE"

echo "  📦 匯出 $JUPYTER_IMAGE → images/jupyter.tar"
docker save -o "$IMAGES_DIR/jupyter.tar" "$JUPYTER_IMAGE"

echo "  📦 匯出 $MONITORING_IMAGE → images/monitoring.tar"
docker save -o "$IMAGES_DIR/monitoring.tar" "$MONITORING_IMAGE"

echo "  📦 匯出 $CODE_SERVER_IMAGE → images/code-server.tar"
docker save -o "$IMAGES_DIR/code-server.tar" "$CODE_SERVER_IMAGE"

echo "  📦 匯出 $ADMIN_IMAGE → images/admin.tar"
docker save -o "$IMAGES_DIR/admin.tar" "$ADMIN_IMAGE"

echo "  📦 匯出 pgvector/pgvector:pg17 → images/pgvector.tar"
docker save -o "$IMAGES_DIR/pgvector.tar" pgvector/pgvector:pg17

echo "  📦 匯出 ghcr.io/open-webui/open-webui:0.7.2 → images/openwebui.tar"
docker save -o "$IMAGES_DIR/openwebui.tar" ghcr.io/open-webui/open-webui:0.7.2

echo "  📦 匯出 quay.io/keycloak/keycloak:26.5.6 → images/keycloak.tar"
docker save -o "$IMAGES_DIR/keycloak.tar" quay.io/keycloak/keycloak:26.5.6

echo "  📦 匯出 nginx:alpine → images/nginx.tar"
docker save -o "$IMAGES_DIR/nginx.tar" nginx:alpine

# --- Step 4: manifest + one compressed bundle ---
MANIFEST="$IMAGES_DIR/IMAGE_MANIFEST.txt"
{
    echo "ISO42001RAG offline image manifest"
    echo "created_at=$(date --iso-8601=seconds)"
    echo "compose_project=iso42001rag"
    echo ""
    echo "file | image | image_id | tar_sha256"
    for spec in "${IMAGE_SPECS[@]}"; do
        file="${spec%%|*}"
        image="${spec#*|}"
        image_id="$(docker image inspect "$image" --format '{{.Id}}')"
        digest="$(sha256sum "$IMAGES_DIR/$file" | awk '{print $1}')"
        echo "$file | $image | $image_id | $digest"
    done
} > "$MANIFEST"

if [[ -z "$BUNDLE_OUTPUT" ]]; then
    BUNDLE_OUTPUT="$SCRIPT_DIR/deploy_packages/iso42001rag-images-$(date '+%Y%m%d-%H%M%S').tar.gz"
elif [[ "$BUNDLE_OUTPUT" != /* ]]; then
    BUNDLE_OUTPUT="$SCRIPT_DIR/$BUNDLE_OUTPUT"
fi
mkdir -p "$(dirname "$BUNDLE_OUTPUT")"
BUNDLE_TMP="${BUNDLE_OUTPUT}.tmp"
rm -f "$BUNDLE_TMP"

bundle_members=(images/IMAGE_MANIFEST.txt)
for spec in "${IMAGE_SPECS[@]}"; do
    bundle_members+=("images/${spec%%|*}")
done
echo ""
echo "⏳ 壓縮 10 個 image tar → $BUNDLE_OUTPUT"
tar -czf "$BUNDLE_TMP" -C "$SCRIPT_DIR" "${bundle_members[@]}"
mv -f "$BUNDLE_TMP" "$BUNDLE_OUTPUT"
(
    cd "$(dirname "$BUNDLE_OUTPUT")"
    sha256sum "$(basename "$BUNDLE_OUTPUT")"
) > "${BUNDLE_OUTPUT}.sha256"

# --- Step 5: 顯示結果 ---
echo ""
echo "=========================================="
echo "  打包完成"
echo "=========================================="
echo ""
ls -lh "$IMAGES_DIR/"
echo ""
TOTAL_SIZE=$(du -sh "$IMAGES_DIR" | cut -f1)
echo "  📁 images/ 總大小: $TOTAL_SIZE"
echo ""
echo "  ✅ 完整 ISO42001 stack images 已打包。"
echo "  🛡️  rag-api image 已確認不含 /app/data、.env 或憑證檔。"
echo "  📦 單一總包: $BUNDLE_OUTPUT"
echo "  🔐 SHA-256:  ${BUNDLE_OUTPUT}.sha256"
echo "  📋 離線部署步驟："
echo "     1. 傳輸乾淨版本程式碼與本總包；不得複製外網 runtime/.env/TLS"
echo "     2. 在內網核對 .sha256，解壓後逐一 docker load"
echo "     3. 使用內網目標機的 .env 執行 ./deploy.sh"
echo "=========================================="
