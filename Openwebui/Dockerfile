# 使用官方建議的穩定版本
FROM ghcr.io/open-webui/open-webui:0.7.2

# 設定環境變數 (後續由 docker-compose 的 env_file 覆蓋)
# 這裡提供預設值作為參考
ENV OPENAI_API_BASE_URLS="http://rag-api:8000/v1;http://172.16.120.67:7000/v1"
ENV WEBUI_URL="https://judge.local"
ENV ENABLE_RAG_WEB_SEARCH="false"
ENV ENABLE_RAG_WEB_LOADER_SSL_VERIFICATION="false"

# 暴露原生的 8080 port，讓內網的 Nginx 能夠反向代理進來
EXPOSE 8080

# 預設執行指令已在 base image 中定義，因此不需特別撰寫 ENTRYPOINT/CMD
