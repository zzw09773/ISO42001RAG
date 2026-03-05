#!/bin/bash
# 安裝新依賴
pip install -r requirements.txt

# 啟動 API 伺服器
# host 0.0.0.0 確保可以從容器外部或 OpenWebUI 訪問
# port 8000 是標準埠口
echo "Starting RAG Agent API on http://0.0.0.0:8000"
python api.py
