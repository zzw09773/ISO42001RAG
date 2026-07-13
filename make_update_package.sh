#!/bin/bash
# =============================================================
# ISO42001 外部稽核準備完整更新包打包腳本
# 用法: ./make_update_package.sh
#
# 產出（deploy_packages/full-stack-update-<date>/）：
#   images/*.tar             完整 stack images
#   full-stack-code.zip      源碼 + 文件 + compose；不含執行期資料/機密
#   MANIFEST.txt             清單 + SHA-256
#
# ⚠ zip 嚴格排除內網執行期資料，避免覆蓋稽核日誌/索引：
#    audit_logs/ processed/ versions/ reports/ alerts*.jsonl .env images/ ssl/ *.tar
# =============================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DATE="$(date '+%Y-%m-%d')"
OUT="deploy_packages/full-stack-update-${DATE}"
IMG="$OUT/images"
mkdir -p "$IMG"

echo "=== 1/4 建置專案 images，並拉取 Compose 核定的外部 images ==="
docker compose build rag-api embed-proxy jupyter monitoring code-server admin
docker compose pull db openwebui keycloak nginx

echo "=== 2/4 匯出完整 stack images ==="
docker save -o "$IMG/rag-api.tar"      iso42001rag-rag-api:latest
docker save -o "$IMG/embed-proxy.tar"  iso42001rag-embed-proxy:latest
docker save -o "$IMG/jupyter.tar"      iso42001rag-jupyter:latest
docker save -o "$IMG/monitoring.tar"   iso42001rag-monitoring:latest
docker save -o "$IMG/code-server.tar"  iso42001rag-code-server:latest
docker save -o "$IMG/admin.tar"        iso42001rag-admin:latest
docker save -o "$IMG/pgvector.tar"     pgvector/pgvector:pg17
docker save -o "$IMG/openwebui.tar"    ghcr.io/open-webui/open-webui:0.7.2
docker save -o "$IMG/keycloak.tar"     quay.io/keycloak/keycloak:26.5.6
docker save -o "$IMG/nginx.tar"        nginx:alpine

echo "=== 3/4 打包源碼 zip（排除執行期資料與機密）==="
ZIP="$OUT/full-stack-code.zip"
rm -f "$ZIP"
zip -rq "$ZIP" \
  RAG monitoring_addon admin_console embed_proxy nginx code-server keycloak tests \
  docker-compose.yaml docker-compose.hardening.yml \
  deploy.sh save_images.sh make_update_package.sh scripts_md2html.py scripts/verify_project.sh \
  .env.example reset_data.sh \
  README.md README.html AUDIT_EVIDENCE_INDEX.md AUDIT_EVIDENCE_INDEX.html \
  PROJECT_STRUCTURE.md PROJECT_STRUCTURE.html INDEX.html \
  -x '*/__pycache__/*' '*.pyc' \
  -x '*/.pytest_cache/*' \
  -x 'RAG/data/audit_logs/*' 'RAG/data/audit_logs_archive*/*' \
  -x 'RAG/data/processed/*' 'RAG/data/versions/*.tar.gz' 'RAG/data/reports/*' \
  -x 'RAG/data/input/*' 'RAG/data/output/*' \
  -x 'monitoring_addon/data/reports/*' 'monitoring_addon/data/reports_archive*/*' \
  -x 'monitoring_addon/data/experiments/*' \
  -x 'monitoring_addon/data/alerts.jsonl' 'monitoring_addon/data/alerts_drift_state.json' \
  -x 'monitoring_addon/data/alerts_health_state.json' 'monitoring_addon/data/availability_log*.jsonl' \
  -x 'monitoring_addon/data/integrity_state.json' \
  -x 'monitoring_addon/data/stability_records.json' \
  -x 'admin_console/data/*' \
  -x 'nginx/ssl/*' \
  -x '*.env' '.env' '.env.bak' \
  -x '*/images/*' '*.tar' '*.zip'
# 說明：保留 golden_dataset.json、drift_baseline.json（穩定證據/基線）、
#       RAG/data/converted_md/*（法規語料，reindex 需要）、所有 .html 文件。

echo "=== 4/4 產生 MANIFEST + SHA-256 ==="
MAN="$OUT/MANIFEST.txt"
{
  echo "ISO42001 外部稽核準備完整更新包"
  echo "=========================="
  echo "產生時間：$(date '+%Y-%m-%d %H:%M %z')"
  echo "服務：db / embed-proxy / rag-api / jupyter / openwebui / keycloak / code-server / nginx / monitoring / admin"
  echo "更新流程：cp .env.example .env，填入強密碼與推論後端後執行 ./deploy.sh"
  echo ""
  echo "檔案 | 角色 | SHA-256"
  echo "----"
  for f in "$ZIP" "$IMG"/*.tar; do
    sz=$(du -h "$f" | cut -f1)
    sha=$(sha256sum "$f" | cut -d' ' -f1)
    echo "$(basename "$f") | $sz | $sha"
  done
} > "$MAN"

echo ""
echo "✅ 完成 → $OUT"
ls -lh "$OUT" "$IMG"
cat "$MAN"
