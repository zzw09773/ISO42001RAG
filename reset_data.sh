#!/usr/bin/env bash
# =============================================================
# 內網試做前清空使用/測試資料 —— 讓單位人員從乾淨狀態開始測試。
#
# 會清空（使用/監測資料）：
#   - 稽核日誌 RAG/data/audit_logs/*.jsonl
#   - 對話歷史 PostgreSQL conversations 表
#   - 監測執行期：alerts.jsonl / alerts_health_state.json / availability_log*.jsonl
#                 integrity_state.json / stability_records.json / reports/* / experiments/*
#
# 會保留（知識庫與參考資料，清掉系統就不能用）：
#   - 向量索引（pgvector）、法規語料 converted_md/
#   - golden_dataset.json（V&V 測試集）
#   - 版本快照 RAG/data/versions/（變更管理紀錄，A.6.2.5）
#
# ⚠ 重要：本腳本會重置稽核日誌的 SHA-256 雜湊鏈。
#    僅限「真實測試開始前」執行一次。真實測試一旦開始，稽核日誌即為
#    ISO 42001 A.6/A.9 證據，不可再清空。
# =============================================================
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"

PGUSER="${POSTGRES_USER:-postgres}"
PGDB="${POSTGRES_DB:-Judge}"

echo "即將清空：稽核日誌、對話歷史、監測告警/報告/實驗。"
echo "保留：向量索引、法規語料、golden_dataset、版本快照。"
read -r -p "確定執行？輸入 yes 繼續：" ans
[ "$ans" = "yes" ] || { echo "已取消，未變更任何資料。"; exit 0; }

echo "── 1/5 先停 rag-api / monitoring ──"
# 必須先停寫入端，避免「清空檔案時舊 process 帶殘留雜湊鏈快取又寫一筆」
# → 否則新檔第一行 prev_hash 不是 genesis，verify_integrity 會誤報「鏈毀損於第一行」。
# （audit_logger._get_prev_hash 已能自癒此情況，停服務是雙重保險。）
docker compose stop rag-api monitoring 2>&1 | tail -2

echo "── 2/5 稽核日誌 ──"
rm -f RAG/data/audit_logs/*.jsonl && echo "  已清空 audit_logs"

echo "── 3/5 對話歷史（conversations）──"
if docker ps --format '{{.Names}}' | grep -q ISO42001_pgvector; then
  docker exec -i ISO42001_pgvector psql -U "$PGUSER" -d "$PGDB" \
    -c "TRUNCATE TABLE conversations;" 2>/dev/null \
    && echo "  已清空 conversations" \
    || echo "  （conversations 表不存在或已空，略過）"
else
  echo "  （pgvector 容器未運行，略過——啟動後可手動 TRUNCATE conversations）"
fi

echo "── 4/5 監測執行期資料 ──"
rm -f monitoring_addon/data/alerts.jsonl \
      monitoring_addon/data/alerts_health_state.json \
      monitoring_addon/data/alerts_drift_state.json \
      monitoring_addon/data/integrity_state.json \
      monitoring_addon/data/availability_log.jsonl \
      monitoring_addon/data/stability_records.json
rm -f monitoring_addon/data/availability_log_*.jsonl 2>/dev/null
rm -f monitoring_addon/data/reports/*.json \
      monitoring_addon/data/reports/*.md \
      monitoring_addon/data/reports/*.html 2>/dev/null
rm -rf monitoring_addon/data/experiments/* 2>/dev/null
echo "  已清空 monitoring 告警/報告/實驗"

echo "── 5/5 啟動服務（乾淨狀態）──"
docker compose up -d rag-api monitoring 2>&1 | tail -2

echo ""
echo "✅ 清空完成。系統現為乾淨狀態，可交付單位人員測試。"
echo "   稽核日誌雜湊鏈已從 genesis 重新開始；之後的每筆查詢都會被記錄為證據。"
echo "   提醒：真實測試開始後請勿再執行本腳本。"
