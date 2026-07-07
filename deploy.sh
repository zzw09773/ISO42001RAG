#!/bin/bash
# =============================================================
# ISO42001 系統 — 外部稽核準備一鍵部署腳本
# 用法: ./deploy.sh
#
# 範圍：啟動完整內網 stack
#   ✓ rag-api, embed-proxy, jupyter, openwebui, keycloak
#   ✓ code-server, nginx, monitoring
#   ✓ db / pgvector（rag-api 基礎依賴）
#
# 功能：
#   1. 自動偵測當前使用者 UID/GID 並寫入 .env
#   2. 若 nginx/ssl 無憑證，產生 aimla.ai.example.com 自簽憑證
#   3. 載入所有離線 Docker images（images/ 資料夾中的 tar，如存在）
#   4. 啟動完整服務組
#   5. 顯示存取入口
# =============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  ISO42001 外部稽核準備部署"
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

upsert_env() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" .env; then
        sed -i "s|^${key}=.*|${key}=${value}|" .env
    else
        printf '\n%s=%s\n' "$key" "$value" >> .env
    fi
}

upsert_env HOST_UID "$CURRENT_UID"
upsert_env HOST_GID "$CURRENT_GID"
upsert_env DOCKER_GID "$DOCKER_GID_NUM"
echo "⚙️  Docker socket GID: $DOCKER_GID_NUM"
echo "✅ UID/GID 已自動寫入 .env"

# --- Step 3: 準備 nginx 自簽憑證 ---
if [ ! -f nginx/ssl/cert.crt ] || [ ! -f nginx/ssl/cert.key ]; then
    echo ""
    echo "⏳ 產生 nginx 自簽憑證..."
    bash nginx/generate_certs.sh
fi

# --- Step 4: 載入離線 Docker images ---
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

# --- Step 5: 確保目錄存在 ---
mkdir -p RAG/data
mkdir -p monitoring_addon/data/reports

# --- Step 6: 啟動完整內網 stack ---
echo ""
echo "⏳ 啟動完整服務組..."
docker compose up -d --build

# --- Step 7: 等待服務啟動 ---
echo ""
echo "⏳ 等待服務啟動..."
sleep 15

# --- Step 8: 顯示結果 ---
echo ""
echo "=========================================="
echo "  服務狀態"
echo "=========================================="
docker compose ps

LOCAL_IP=$(hostname -I | awk '{print $1}')
NGINX_HTTP_PORT=$(grep '^NGINX_HTTP_PORT=' .env | tail -1 | cut -d= -f2-)
NGINX_HTTPS_PORT=$(grep '^NGINX_HTTPS_PORT=' .env | tail -1 | cut -d= -f2-)
OPENWEBUI_PORT=$(grep '^OPENWEBUI_PORT=' .env | tail -1 | cut -d= -f2-)
KEYCLOAK_PORT=$(grep '^KEYCLOAK_PORT=' .env | tail -1 | cut -d= -f2-)
CODESERVER_PORT=$(grep '^CODESERVER_PORT=' .env | tail -1 | cut -d= -f2-)
JUPYTER_PORT=$(grep '^JUPYTER_PORT=' .env | tail -1 | cut -d= -f2-)

NGINX_HTTP_PORT=${NGINX_HTTP_PORT:-8088}
NGINX_HTTPS_PORT=${NGINX_HTTPS_PORT:-8443}
OPENWEBUI_PORT=${OPENWEBUI_PORT:-18088}
KEYCLOAK_PORT=${KEYCLOAK_PORT:-18080}
CODESERVER_PORT=${CODESERVER_PORT:-18443}
JUPYTER_PORT=${JUPYTER_PORT:-25678}

echo ""
echo "=========================================="
echo "  存取方式"
echo "=========================================="
echo "  🌐 Nginx HTTPS/OpenWebUI: https://aimla.ai.example.com:${NGINX_HTTPS_PORT}/"
echo "  🌐 Nginx HTTP redirect:   http://${LOCAL_IP}:${NGINX_HTTP_PORT}/"
echo "  💬 OpenWebUI direct:      http://${LOCAL_IP}:${OPENWEBUI_PORT}/"
echo "  🔐 Keycloak:              http://${LOCAL_IP}:${KEYCLOAK_PORT}/"
echo "  🧑‍💻 Code Server:          http://${LOCAL_IP}:${CODESERVER_PORT}/"
echo "  🔧 RAG API:               http://${LOCAL_IP}:8043/health"
echo "  📊 Monitoring direct:     http://${LOCAL_IP}:8200/dashboard"
echo "  📊 Monitoring via nginx:  https://aimla.ai.example.com:${NGINX_HTTPS_PORT}/monitoring/"
echo "  📓 Jupyter:               http://${LOCAL_IP}:${JUPYTER_PORT}/"
echo "  🗄️  pgvector:         postgresql://postgres:postgres@${LOCAL_IP}:15432/Judge"
echo ""
echo "  👤 UID/GID:          ${CURRENT_UID}:${CURRENT_GID}"
echo ""
echo "  📋 下一步建議："
echo "     1. 索引法規：docker exec -w /home/jovyan/work ISO42001_jupyter python scripts/reindex.py"
echo "     2. 跑首次 V&V：docker exec ISO42001_monitoring python scripts/run_online_vv.py --rag-url http://rag-api:8000"
echo "     3. 到 Keycloak 建 realm/client 或匯入設定，讓 OpenWebUI 走 OIDC 註冊"
echo "=========================================="
