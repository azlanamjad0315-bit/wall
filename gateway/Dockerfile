FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /data && chown -R app:app /data /app
USER app
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=6s --start-period=15s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/healthz',timeout=5)"
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8787","--proxy-headers"]
