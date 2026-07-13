#!/bin/bash
# =============================================================
# ISO42001 系統 — 外部稽核準備一鍵部署腳本
# 用法: ./deploy.sh
#
# 範圍：啟動完整內網 stack
#   ✓ rag-api, embed-proxy, jupyter, openwebui, keycloak
#   ✓ code-server, nginx, monitoring, admin
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

# Admin 掛載 Docker socket，必須先有憑證卡白名單或明確啟用 break-glass。
env_value() {
    local value
    value=$(sed -n "s/^[[:space:]]*${1}[[:space:]]*=[[:space:]]*//p" .env | tail -1)
    value=$(printf '%s' "$value" | sed 's/[[:space:]]*$//')
    case "$value" in
        \"*\")
            value="${value#\"}"
            value="${value%\"}"
            ;;
        \'*\')
            value="${value#\'}"
            value="${value%\'}"
            ;;
    esac
    printf '%s\n' "$value"
}

ADMIN_CARD_SERIALS_VALUE=$(env_value ADMIN_CARD_SERIALS | tr -d '[:space:]')
PASSWORD_FALLBACK_VALUE=$(env_value ENABLE_PASSWORD_FALLBACK | tr '[:upper:]' '[:lower:]')
PASSWORD_FALLBACK_ENABLED=false
case "$PASSWORD_FALLBACK_VALUE" in
    true|1|yes) PASSWORD_FALLBACK_ENABLED=true ;;
esac
if [ -z "$ADMIN_CARD_SERIALS_VALUE" ] && [ "$PASSWORD_FALLBACK_ENABLED" != "true" ]; then
    echo "❌ Admin 無登入途徑：請在 .env 設定 ADMIN_CARD_SERIALS，或明確啟用 ENABLE_PASSWORD_FALLBACK=true。"
    exit 1
fi
if [ "$PASSWORD_FALLBACK_ENABLED" = "true" ] && \
   { [ -z "$(env_value ADMIN_USERNAME)" ] || [ -z "$(env_value ADMIN_PASSWORD)" ]; }; then
    echo "❌ 已啟用 Admin break-glass，但 ADMIN_USERNAME / ADMIN_PASSWORD 尚未完整設定。"
    exit 1
fi

# 具 Docker/資料庫/OIDC 控制權的服務不得使用公開範本值啟動。
PLACEHOLDER_SECRET_KEYS=(
    POSTGRES_PASSWORD
    WEBUI_SECRET_KEY
    KEYCLOAK_ADMIN_PASSWORD
    OAUTH_CLIENT_SECRET
    CODESERVER_PASSWORD
    CODESERVER_SUDO_PASSWORD
)
PLACEHOLDER_KEYS=()
for key in "${PLACEHOLDER_SECRET_KEYS[@]}"; do
    value=$(env_value "$key")
    normalized=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    case "$normalized" in
        ""|postgres|admin|password|\<*\>|*change-this*|*your-secret-key-here*|*dev-client-secret*)
            PLACEHOLDER_KEYS+=("$key")
            ;;
    esac
done
if [ "${#PLACEHOLDER_KEYS[@]}" -gt 0 ] && [ "${ALLOW_PLACEHOLDER_SECRETS:-false}" != "true" ]; then
    echo "❌ 下列機密仍為空值或公開範本值，拒絕啟動："
    printf '   - %s\n' "${PLACEHOLDER_KEYS[@]}"
    echo "   請在 .env 透過核准的機密流程填入強隨機值；實際值不會顯示。"
    exit 1
fi
if [ "${#PLACEHOLDER_KEYS[@]}" -gt 0 ]; then
    echo "⚠️  已由操作人員明確接受既有範本機密風險（ALLOW_PLACEHOLDER_SECRETS=true）；本次不輪替。"
fi

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

# 只把 Admin 可管理的非秘密白名單同步給 rag-api。根 .env 不掛進 RAG 容器，
# 避免 Keycloak/code-server/Admin 等憑證跨越服務邊界。
RAG_RUNTIME_DIR="admin_console/data"
RAG_RUNTIME_ENV="${RAG_RUNTIME_DIR}/rag-runtime.env"
mkdir -p "$RAG_RUNTIME_DIR"
RAG_RUNTIME_TMP=$(mktemp "${RAG_RUNTIME_DIR}/.rag-runtime.env.XXXXXX")
chmod 600 "$RAG_RUNTIME_TMP"
for key in \
    CHAT_MODEL_NAME TOP_K RERANK_TOP_N REASONING_EFFORT REACT_MODE \
    CHUNK_SIZE MAX_RETRIEVAL_TOKENS RATE_LIMIT_PER_MINUTE \
    RAG_LOG_LEVEL RAG_LOG_VERBOSE LLM_API_BASE EMBED_API_BASE EMBED_MODEL_NAME
do
    if grep -Eq "^[[:space:]]*${key}[[:space:]]*=" .env; then
        printf '%s=%s\n' "$key" "$(env_value "$key")" >> "$RAG_RUNTIME_TMP"
    fi
done
mv -f "$RAG_RUNTIME_TMP" "$RAG_RUNTIME_ENV"
chmod 600 "$RAG_RUNTIME_ENV"
echo "✅ RAG runtime 白名單設定已同步（不含金鑰與管理密碼）"

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

# deploy.sh 明確禁止 build/pull，避免離線現場意外連網或產生未核定 image。
# 因此在啟動前先列出所有缺少的 image，提供可操作的錯誤訊息。
REQUIRED_IMAGES=(
    iso42001rag-rag-api:latest
    iso42001rag-embed-proxy:latest
    iso42001rag-jupyter:latest
    iso42001rag-monitoring:latest
    iso42001rag-code-server:latest
    iso42001rag-admin:latest
    pgvector/pgvector:pg17
    ghcr.io/open-webui/open-webui:0.7.2
    quay.io/keycloak/keycloak:26.5.6
    nginx:alpine
)
MISSING_IMAGES=()
for image in "${REQUIRED_IMAGES[@]}"; do
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        MISSING_IMAGES+=("$image")
    fi
done
if [ "${#MISSING_IMAGES[@]}" -gt 0 ]; then
    echo "❌ 缺少部署所需 Docker images；本腳本在離線模式不會自動 build/pull："
    printf '   - %s\n' "${MISSING_IMAGES[@]}"
    echo "   請先把完整更新包的 tar 放入 images/，或在可連網建置機執行 ./make_update_package.sh。"
    exit 1
fi
echo "✅ 10 個服務所需 images 已就緒"

# --- Step 5: 確保目錄存在 ---
mkdir -p RAG/data
mkdir -p monitoring_addon/data/reports

# --- Step 6: 啟動完整內網 stack，並等待 healthcheck ---
echo ""
echo "⏳ 啟動完整服務組..."
docker compose up -d --wait --wait-timeout "${DEPLOY_WAIT_TIMEOUT:-300}" --no-build --pull never

# --- Step 7: 顯示結果 ---
echo ""
echo "=========================================="
echo "  服務狀態"
echo "=========================================="
docker compose ps

LOCAL_IP=$(hostname -I | awk '{print $1}')
NGINX_HTTP_PORT=$(env_value NGINX_HTTP_PORT)
NGINX_HTTPS_PORT=$(env_value NGINX_HTTPS_PORT)
OPENWEBUI_PORT=$(env_value OPENWEBUI_PORT)
KEYCLOAK_PORT=$(env_value KEYCLOAK_PORT)
CODESERVER_PORT=$(env_value CODESERVER_PORT)
JUPYTER_PORT=$(env_value JUPYTER_PORT)
ADMIN_PORT=$(env_value ADMIN_PORT)
POSTGRES_USER_VALUE=$(env_value POSTGRES_USER)
POSTGRES_DB_VALUE=$(env_value POSTGRES_DB)

NGINX_HTTP_PORT=${NGINX_HTTP_PORT:-8088}
NGINX_HTTPS_PORT=${NGINX_HTTPS_PORT:-8443}
OPENWEBUI_PORT=${OPENWEBUI_PORT:-18088}
KEYCLOAK_PORT=${KEYCLOAK_PORT:-18080}
CODESERVER_PORT=${CODESERVER_PORT:-18443}
JUPYTER_PORT=${JUPYTER_PORT:-25678}
ADMIN_PORT=${ADMIN_PORT:-8300}
POSTGRES_USER_VALUE=${POSTGRES_USER_VALUE:-postgres}
POSTGRES_DB_VALUE=${POSTGRES_DB_VALUE:-Judge}

echo ""
echo "=========================================="
echo "  存取方式"
echo "=========================================="
echo "  🌐 Nginx HTTPS/OpenWebUI: https://aimla.ai.example.com:${NGINX_HTTPS_PORT}/"
echo "  🌐 Nginx HTTP redirect:   http://${LOCAL_IP}:${NGINX_HTTP_PORT}/"
echo "  💬 OpenWebUI direct:      http://${LOCAL_IP}:${OPENWEBUI_PORT}/"
echo "  🔐 Keycloak:              http://${LOCAL_IP}:${KEYCLOAK_PORT}/"
echo "  🧑‍💻 Code Server:          http://${LOCAL_IP}:${CODESERVER_PORT}/"
echo "  🛠️  Admin console:        http://${LOCAL_IP}:${ADMIN_PORT}/"
echo "  🔧 RAG API:               http://${LOCAL_IP}:8043/health"
echo "  📊 Monitoring direct:     http://${LOCAL_IP}:8200/dashboard"
echo "  📊 Monitoring via nginx:  https://aimla.ai.example.com:${NGINX_HTTPS_PORT}/monitoring/"
echo "  📓 Jupyter:               http://${LOCAL_IP}:${JUPYTER_PORT}/"
echo "  🗄️  pgvector:         postgresql://${POSTGRES_USER_VALUE}@${LOCAL_IP}:15432/${POSTGRES_DB_VALUE}（密碼不顯示）"
echo ""
echo "  👤 UID/GID:          ${CURRENT_UID}:${CURRENT_GID}"
echo ""
echo "  📋 下一步建議："
echo "     1. 索引法規：docker exec -w /home/jovyan/work ISO42001_jupyter python scripts/reindex.py"
echo "     2. 跑首次 V&V：docker exec ISO42001_monitoring python scripts/run_online_vv.py --rag-url http://rag-api:8000"
echo "     3. 到 Keycloak 建 realm/client 或匯入設定，讓 OpenWebUI 走 OIDC 註冊"
echo "=========================================="
