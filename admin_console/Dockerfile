FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY admincore/ admincore/
COPY service/ service/
ENV ADMIN_RUNTIME=1
EXPOSE 8300
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8300/health', timeout=3)"]
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8300"]
