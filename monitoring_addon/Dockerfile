# =============================================================
# ISO42001 Monitoring Addon — FastAPI 即時儀表板服務
# port 8200，read-only 掛載 RAG/data
# =============================================================
FROM python:3.11-slim

WORKDIR /app

# 系統相依（curl 給 healthcheck 用，可選；numpy/requests 編譯依賴）
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python 相依
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 應用程式碼
COPY monitoring/ /app/monitoring/
COPY scripts/    /app/scripts/
COPY service/    /app/service/

# 只烤入「參考資料」——golden_dataset（V&V 測試集）。
# 刻意不烤 dev 的執行期資料（reports / alerts / experiments / stability），
# 確保全新部署 image 內為乾淨狀態（無任何 dev 使用/監測紀錄）。reports 目錄
# 由 app 執行期自建；正式部署另以 bind mount 覆蓋 /app/data。
# （drift_baseline.json 已隨分布漂移移除——改為服務健康監測。）
COPY data/golden_dataset.json /app/data/golden_dataset.json
RUN mkdir -p /app/data/reports

# RAG 主系統的 audit_logs 與 reports 透過 docker volume 掛載到此路徑
ENV RAG_DATA_DIR=/rag_data \
    MONITORING_PORT=8200 \
    PYTHONUNBUFFERED=1

EXPOSE 8200

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
    CMD curl -fsS http://localhost:8200/health || exit 1

CMD ["python", "-m", "uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8200"]
