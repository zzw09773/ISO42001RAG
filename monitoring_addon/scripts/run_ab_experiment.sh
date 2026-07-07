#!/usr/bin/env bash
# A/B 效能驗證編排：改進前(舊prompt) → 重啟 → 改進後(新prompt) → 回歸gate → 歸因
# 一次跑到底，以報告檔存在為完成訊號（不靠 pgrep，避免自我匹配）。
set -u
cd /home/c1147259/桌面/ISO42001/ISO42001RAG/ISO42001Deploy/monitoring_addon
LOG=/tmp/ab_experiment.log
RAG=http://localhost:8043
TIMEOUT=180
: > "$LOG"

say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

backend_up() { timeout 8 curl -sk https://172.16.120.35/v1/models -o /dev/null -w "%{http_code}" 2>/dev/null | grep -qvE '^(000|)$'; }

say "=== A/B 實驗開始 ==="
if ! backend_up; then say "❌ 後端不可達，中止"; exit 1; fi

# ── 1. 改進前（rag-api 仍為舊 prompt 1.1.0，未重啟）──────────────────
rm -f data/reports_before/online_vv_*.json
say "步驟1：改進前 V&V（舊 prompt 1.1.0）…"
python3 scripts/run_online_vv.py --rag-url "$RAG" --timeout "$TIMEOUT" --sleep-ms 300 \
  --output-dir data/reports_before >> "$LOG" 2>&1
BEFORE=$(ls -t data/reports_before/online_vv_*.json 2>/dev/null | head -1)
if [ -z "$BEFORE" ]; then say "❌ 改進前報告未產出，中止"; exit 1; fi
HR_B=$(python3 -c "import json;print(json.load(open('$BEFORE'))['aggregate']['metrics']['hit_rate'])")
say "步驟1完成：改進前 Hit Rate = $HR_B  ($BEFORE)"

# ── 2. 重啟 rag-api 載入新 prompt 1.2.0 ─────────────────────────────
say "步驟2：重啟 rag-api 載入新 prompt 1.2.0…"
docker restart ISO42001_rag_api >> "$LOG" 2>&1
for i in $(seq 1 60); do
  curl -fsS "$RAG/health" >/dev/null 2>&1 && break; sleep 3
done
say "步驟2完成：rag-api healthy"
# 確認新 prompt 生效
docker exec -i ISO42001_rag_api python3 -c "import sys;sys.path.insert(0,'/app');from rag_system.core.prompts import PROMPT_VERSIONS;print('SYSTEM_PROMPT_BASELINE='+PROMPT_VERSIONS['SYSTEM_PROMPT_BASELINE'])" >> "$LOG" 2>&1

# ── 3. 改進後 ───────────────────────────────────────────────────────
rm -f data/reports_after/online_vv_*.json
say "步驟3：改進後 V&V（新 prompt 1.2.0）…"
python3 scripts/run_online_vv.py --rag-url "$RAG" --timeout "$TIMEOUT" --sleep-ms 300 \
  --output-dir data/reports_after >> "$LOG" 2>&1
AFTER=$(ls -t data/reports_after/online_vv_*.json 2>/dev/null | head -1)
if [ -z "$AFTER" ]; then say "❌ 改進後報告未產出，中止"; exit 1; fi
HR_A=$(python3 -c "import json;print(json.load(open('$AFTER'))['aggregate']['metrics']['hit_rate'])")
say "步驟3完成：改進後 Hit Rate = $HR_A  ($AFTER)"

# ── 4. 回歸 gate（前 vs 後）────────────────────────────────────────
say "步驟4：回歸 gate 比對…"
python3 scripts/run_regression_gate.py --baseline "$BEFORE" --current "$AFTER" \
  --tag promptfix-citation-grounding --no-rerun >> "$LOG" 2>&1
say "步驟4完成"

# ── 5. 歸因（改進後）────────────────────────────────────────────────
say "步驟5：改進後歸因…"
python3 scripts/run_attribution.py --vv-report "$AFTER" \
  --audit-dir /rag_data/audit_logs --golden data/golden_dataset.json >> "$LOG" 2>&1 || \
  docker exec -i ISO42001_monitoring python3 /app/scripts/run_attribution.py \
    --vv-report "/app/data/reports_after/$(basename $AFTER)" \
    --audit-dir /rag_data/audit_logs --golden /app/data/golden_dataset.json >> "$LOG" 2>&1
say "步驟5完成"

say "=== 實驗完成：改進前 HR=$HR_B → 改進後 HR=$HR_A ==="
echo "DONE" > /tmp/ab_experiment.done
