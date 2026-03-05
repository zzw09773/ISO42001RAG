#!/bin/bash
# =============================================================
# ISO42001 系統 — 離線 Image 打包腳本
# 用法: ./save_images.sh
#
# 功能：
#   1. 先 build rag-api 和 jupyter images（含 pip install）
#   2. 將所有 6 個 images 匯出為 tar 至 images/
#   3. 打包後整個 ISO42001Deploy/ 即為完整離線部署包
# =============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGES_DIR="$SCRIPT_DIR/images"
mkdir -p "$IMAGES_DIR"

echo "=========================================="
echo "  離線 Image 打包"
echo "=========================================="

# --- Step 1: Build 需要建置的 images ---
echo ""
echo "⏳ 建置 rag-api, embed-proxy 和 jupyter images（包含 pip install）..."
docker compose build rag-api embed-proxy jupyter
echo "✅ Build 完成"

# --- Step 2: 匯出所有 images ---
echo ""
echo "⏳ 匯出所有 images..."

# 需要拉取/確認的外部 images
declare -A EXTERNAL_IMAGES=(
    ["pgvector"]="pgvector/pgvector:pg17"
    ["nginx"]="nginx:alpine"
    ["openwebui"]="ghcr.io/open-webui/open-webui:0.7.2"
)

for name in "${!EXTERNAL_IMAGES[@]}"; do
    img="${EXTERNAL_IMAGES[$name]}"
    if ! docker image inspect "$img" &>/dev/null; then
        echo "  📥 拉取 $img ..."
        docker pull "$img"
    fi
    echo "  📦 匯出 $img → images/${name}.tar"
    docker save -o "$IMAGES_DIR/${name}.tar" "$img"
done

# 匯出 build 的 images（名稱由 docker compose 決定）
RAG_IMAGE="iso42001deploy-rag-api:latest"
JUPYTER_IMAGE="iso42001deploy-jupyter:latest"
EMBED_PROXY_IMAGE="iso42001deploy-embed-proxy:latest"

echo "  📦 匯出 $RAG_IMAGE → images/rag-api.tar"
docker save -o "$IMAGES_DIR/rag-api.tar" "$RAG_IMAGE"

echo "  📦 匯出 $EMBED_PROXY_IMAGE → images/embed-proxy.tar"
docker save -o "$IMAGES_DIR/embed-proxy.tar" "$EMBED_PROXY_IMAGE"

echo "  📦 匯出 $JUPYTER_IMAGE → images/jupyter.tar"
docker save -o "$IMAGES_DIR/jupyter.tar" "$JUPYTER_IMAGE"

# --- Step 3: 顯示結果 ---
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
echo "  ✅ 所有 images 已打包（含 pip 套件）。"
echo "  📋 離線部署步驟："
echo "     1. 將整個 ISO42001Deploy/ 複製到內網機器"
echo "     2. 執行 ./deploy.sh"
echo "=========================================="
