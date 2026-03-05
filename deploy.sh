#!/bin/bash
# =============================================================
# ISO42001 系統 — 一鍵部署腳本
# 用法: ./deploy.sh
#
# 功能：
#   1. 自動偵測當前使用者 UID/GID 並寫入 .env
#   2. 載入所有離線 Docker images（images/ 資料夾中的 tar）
#   3. 建置並啟動 6 個服務
#   4. 等待健康檢查通過
# =============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  ISO42001 系統部署"
echo "=========================================="

# --- Step 1: 檢查 .env ---
if [ ! -f .env ]; then
    echo "❌ 找不到 .env 檔案，請先建立。"
    exit 1
fi
echo "✅ .env 已存在"

# --- Step 2: 自動偵測 UID/GID 並寫入 .env ---
CURRENT_UID=$(id -u)
CURRENT_GID=$(id -g)
DOCKER_GID_NUM=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo "988")

echo "⚙️  偵測到使用者: $(whoami) (UID=$CURRENT_UID, GID=$CURRENT_GID)"

# 使用 sed 更新 .env 中的 HOST_UID/HOST_GID/DOCKER_GID
sed -i "s/^HOST_UID=.*/HOST_UID=$CURRENT_UID/" .env
sed -i "s/^HOST_GID=.*/HOST_GID=$CURRENT_GID/" .env
sed -i "s/^DOCKER_GID=.*/DOCKER_GID=$DOCKER_GID_NUM/" .env
echo "⚙️  Docker socket GID: $DOCKER_GID_NUM"
echo "✅ UID/GID 已自動寫入 .env"

# --- Step 3: 載入離線 Docker images ---
echo ""
echo "⏳ 載入離線 Docker images..."

# 載入 images/ 資料夾中的所有 tar
if [ -d "images" ]; then
    for tar_file in images/*.tar; do
        [ -f "$tar_file" ] || continue
        echo "  📦 載入 $tar_file ..."
        docker load < "$tar_file"
    done
    echo "✅ images/ 資料夾中所有 image 已載入"
fi



# --- Step 4: 確保目錄存在且權限正確 ---
mkdir -p RAG/data

# --- Step 5: 建置並啟動所有服務 ---
RAG_IMAGE="iso42001deploy-rag-api:latest"
JUPYTER_IMAGE="iso42001deploy-jupyter:latest"
EMBED_PROXY_IMAGE="iso42001deploy-embed-proxy:latest"

if docker image inspect "$RAG_IMAGE" &>/dev/null && \
   docker image inspect "$JUPYTER_IMAGE" &>/dev/null && \
   docker image inspect "$EMBED_PROXY_IMAGE" &>/dev/null; then
    echo "✅ 已偵測到預建 images（含 pip 套件），跳過 build（離線模式）"
    docker compose up -d
else
    echo "⚠️  未找到預建 images，執行 build（需要網路）..."
    docker compose up -d --build
fi

# --- Step 5: 等待健康檢查 ---
echo ""
echo "⏳ 等待服務啟動..."
sleep 10

MAX_WAIT=120
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    HEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"healthy"' || true)
    TOTAL=$(docker compose ps --format json 2>/dev/null | wc -l || true)
    echo "  健康狀態: $HEALTHY/$TOTAL 服務就緒 (已等待 ${WAITED}s)"

    # 至少 db 和 rag-api 需要 healthy
    if [ "$HEALTHY" -ge 2 ]; then
        break
    fi
    sleep 10
    WAITED=$((WAITED + 10))
done

# --- Step 6: 顯示結果 ---
echo ""
echo "=========================================="
echo "  服務狀態"
echo "=========================================="
docker compose ps

LOCAL_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=========================================="
echo "  存取方式"
echo "=========================================="
echo "  🌐 Open WebUI:  https://${LOCAL_IP}/"
echo "  🔧 RAG API:     http://${LOCAL_IP}:8000/health"
echo "  📓 Jupyter:     http://${LOCAL_IP}:25678/"
echo "  🗄️  pgvector:    postgresql://postgres:postgres@${LOCAL_IP}:15432/Judge"
echo ""
echo "  👤 UID/GID:     ${CURRENT_UID}:${CURRENT_GID}"
echo "  ⚠️  HTTPS 使用自簽憑證，瀏覽器會出現安全警告，請選擇「繼續前往」。"
echo "=========================================="
