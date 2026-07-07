#!/bin/bash
# =============================================================
# ISO42001 系統 — 離線 Image 打包腳本
# 用法: ./save_images.sh
#
# 範圍：完整內網 stack 所需 images
#   ✓ rag-api / embed-proxy / jupyter / monitoring / code-server（本專案 build）
#   ✓ pgvector / openwebui:0.7.2 / keycloak:26.5.6 / nginx（固定版本基礎 image）
# =============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGES_DIR="$SCRIPT_DIR/images"
mkdir -p "$IMAGES_DIR"

echo "=========================================="
echo "  離線 Image 打包（完整 ISO42001 stack）"
echo "=========================================="

# --- Step 1: Build project images ---
echo ""
echo "⏳ 建置 rag-api、embed-proxy、jupyter、monitoring、code-server images..."
docker compose build rag-api embed-proxy jupyter monitoring code-server
echo "✅ Build 完成"

# --- Step 2: Pull pinned external images ---
echo ""
echo "⏳ 拉取 pgvector、openwebui、keycloak、nginx images..."
docker compose pull db openwebui keycloak nginx
echo "✅ Pull 完成"

# --- Step 3: 匯出 images ---
echo ""
echo "⏳ 匯出 images..."

RAG_IMAGE="iso42001deploy-rag-api:latest"
EMBED_PROXY_IMAGE="iso42001deploy-embed-proxy:latest"
JUPYTER_IMAGE="iso42001deploy-jupyter:latest"
MONITORING_IMAGE="iso42001deploy-monitoring:latest"
CODE_SERVER_IMAGE="iso42001deploy-code-server:latest"

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

echo "  📦 匯出 pgvector/pgvector:pg17 → images/pgvector.tar"
docker save -o "$IMAGES_DIR/pgvector.tar" pgvector/pgvector:pg17

echo "  📦 匯出 ghcr.io/open-webui/open-webui:0.7.2 → images/openwebui.tar"
docker save -o "$IMAGES_DIR/openwebui.tar" ghcr.io/open-webui/open-webui:0.7.2

echo "  📦 匯出 quay.io/keycloak/keycloak:26.5.6 → images/keycloak.tar"
docker save -o "$IMAGES_DIR/keycloak.tar" quay.io/keycloak/keycloak:26.5.6

echo "  📦 匯出 nginx:alpine → images/nginx.tar"
docker save -o "$IMAGES_DIR/nginx.tar" nginx:alpine

# --- Step 4: 顯示結果 ---
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
echo "  📋 離線部署步驟："
echo "     1. 將整個 ISO42001Deploy/ 複製到內網機器"
echo "     2. 執行 ./deploy.sh"
echo "=========================================="
