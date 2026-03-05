FROM quay.io/jupyter/minimal-notebook:2025-09-30

# UID/GID 由執行時的 NB_UID/NB_GID 環境變數控制
# 不在 build time 寫死，確保 image 可在不同機器上使用

COPY requirements.txt /tmp/requirements.txt

USER root

# 必要套件：PostgreSQL client、Python 依賴、sudo 權限
RUN apt-get update && apt-get install -yq build-essential libpq-dev && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt && \
    echo "jovyan ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN chmod -R a+rX /home/jovyan/work || true

WORKDIR /home/jovyan/work

# 以 root 啟動，讓官方 entrypoint 根據 NB_UID/NB_GID 環境變數切換使用者
CMD ["start-notebook.py", "--NotebookApp.token=''", "--NotebookApp.password=''"]
