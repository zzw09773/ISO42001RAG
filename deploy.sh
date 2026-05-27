#!/bin/bash
# =============================================================
# ISO42001 系統 — 一鍵部署腳本
# 用法: ./deploy.sh
#
# 範圍：只啟動 RAG + monitoring，自動拉起 db、embed-proxy
#   ✓ rag-api      (port 8043 → 容器 8000)
#   ✓ jupyter      (port 25678)
#   ✓ monitoring   (port 8200 dashboard)
#   ✓ db / pgvector       (透過 depends_on 自動帶起)
#   ✓ embed-proxy         (透過 depends_on 自動帶起)
#   ✗ openwebui / nginx — 內網側自行管理前端代理，本腳本不啟動
#
# 功能：
#   1. 自動偵測當前使用者 UID/GID 並寫入 .env
#   2. 載入所有離線 Docker images（images/ 資料夾中的 tar）
#   3. 啟動 RAG + monitoring 服務組
#   4. 等待健康檢查通過
# =============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  ISO42001 系統部署（RAG + Monitoring）"
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

sed -i "s/^HOST_UID=.*/HOST_UID=$CURRENT_UID/" .env
sed -i "s/^HOST_GID=.*/HOST_GID=$CURRENT_GID/" .env
sed -i "s/^DOCKER_GID=.*/DOCKER_GID=$DOCKER_GID_NUM/" .env 2>/dev/null || true
echo "⚙️  Docker socket GID: $DOCKER_GID_NUM"
echo "✅ UID/GID 已自動寫入 .env"

# --- Step 3: 載入離線 Docker images ---
echo ""
echo "⏳ 載入離線 Docker images..."

if [ -d "images" ]; then
    for tar_file in images/*.tar; do
        [ -f "$tar_file" ] || continue
        echo "  📦 載入 $tar_file ..."
        docker load < "$tar_file"
    done
    echo "✅ images/ 資料夾中所有 image 已載入"
fi

# --- Step 4: 確保目錄存在 ---
mkdir -p RAG/data
mkdir -p monitoring_addon/data/reports

# --- Step 5: 啟動 RAG + monitoring 服務組 ---
RAG_IMAGE="iso42001deploy-rag-api:latest"
JUPYTER_IMAGE="iso42001deploy-jupyter:latest"
MONITORING_IMAGE="iso42001deploy-monitoring:latest"

if docker image inspect "$RAG_IMAGE" &>/dev/null && \
   docker image inspect "$JUPYTER_IMAGE" &>/dev/null && \
   docker image inspect "$MONITORING_IMAGE" &>/dev/null; then
    echo "✅ 已偵測到預建 images（離線模式），跳過 build"
    docker compose up -d rag-api jupyter monitoring
else
    echo "⚠️  未找到預建 images，執行 build（需要網路）..."
    docker compose up -d --build rag-api jupyter monitoring
fi

# --- Step 6: 等待健康檢查 ---
echo ""
echo "⏳ 等待服務啟動..."
sleep 10

MAX_WAIT=180
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    HEALTHY=$(docker compose ps --format json 2>/dev/null | grep -c '"healthy"' || true)
    TOTAL=$(docker compose ps --format json 2>/dev/null | wc -l || true)
    echo "  健康狀態: $HEALTHY/$TOTAL 服務就緒 (已等待 ${WAITED}s)"

    # db + rag-api + monitoring 至少 3 個 healthy（embed-proxy 也會 healthy 但這裡寬鬆）
    if [ "$HEALTHY" -ge 3 ]; then
        break
    fi
    sleep 10
    WAITED=$((WAITED + 10))
done

# --- Step 7: 顯示結果 ---
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
echo "  🔧 RAG API:         http://${LOCAL_IP}:8043/health"
echo "  📊 Monitoring 儀表板: http://${LOCAL_IP}:8200/dashboard"
echo "  📓 Jupyter:          http://${LOCAL_IP}:25678/"
echo "  🗄️  pgvector:         postgresql://postgres:postgres@${LOCAL_IP}:15432/Judge"
echo ""
echo "  👤 UID/GID:          ${CURRENT_UID}:${CURRENT_GID}"
echo ""
echo "  📋 下一步建議："
echo "     1. 索引法規：docker exec -w /home/jovyan/work ISO42001_jupyter python scripts/reindex.py"
echo "     2. 跑首次 V&V：docker exec ISO42001_monitoring python scripts/run_online_vv.py --rag-url http://rag-api:8000"
echo "     3. 開儀表板：http://${LOCAL_IP}:8200/dashboard"
echo "=========================================="
