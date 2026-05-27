#!/bin/bash
# =============================================================
# ISO42001 系統 — 離線 Image 打包腳本
# 用法: ./save_images.sh
#
# 範圍：只打包 RAG + monitoring 相關 images
#   ✓ rag-api      (iso42001deploy-rag-api)
#   ✓ jupyter      (iso42001deploy-jupyter)
#   ✓ monitoring   (iso42001deploy-monitoring)
#   ✗ openwebui / nginx — 內網側自行處理前端代理，本腳本不打包
#
# pgvector / embed-proxy 等基礎服務若也要離線部署，可自行加入
# 但通常內網已有相容映像，這裡保持精簡。
# =============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

IMAGES_DIR="$SCRIPT_DIR/images"
mkdir -p "$IMAGES_DIR"

echo "=========================================="
echo "  離線 Image 打包（RAG + Monitoring）"
echo "=========================================="

# --- Step 1: Build rag-api、jupyter、monitoring ---
echo ""
echo "⏳ 建置 rag-api、jupyter、monitoring images..."
docker compose build rag-api jupyter monitoring
echo "✅ Build 完成"

# --- Step 2: 匯出 images ---
echo ""
echo "⏳ 匯出 images..."

RAG_IMAGE="iso42001deploy-rag-api:latest"
JUPYTER_IMAGE="iso42001deploy-jupyter:latest"
MONITORING_IMAGE="iso42001deploy-monitoring:latest"

echo "  📦 匯出 $RAG_IMAGE → images/rag-api.tar"
docker save -o "$IMAGES_DIR/rag-api.tar" "$RAG_IMAGE"

echo "  📦 匯出 $JUPYTER_IMAGE → images/jupyter.tar"
docker save -o "$IMAGES_DIR/jupyter.tar" "$JUPYTER_IMAGE"

echo "  📦 匯出 $MONITORING_IMAGE → images/monitoring.tar"
docker save -o "$IMAGES_DIR/monitoring.tar" "$MONITORING_IMAGE"

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
echo "  ✅ 所有 RAG + monitoring images 已打包。"
echo "  📋 離線部署步驟："
echo "     1. 將整個 ISO42001Deploy/ 複製到內網機器"
echo "     2. 執行 ./deploy.sh"
echo "=========================================="
