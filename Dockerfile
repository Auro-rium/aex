FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AEX_CONFIG_DIR=/etc/aex/config \
    AEX_LOG_DIR=/var/log/aex

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY docker/models.yaml /etc/aex/config/models.yaml

RUN pip install --upgrade pip && pip install .

RUN mkdir -p /var/log/aex

EXPOSE 9000

HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=3).read()"

CMD ["sh", "-c", "uvicorn aex.daemon.app:app --host 0.0.0.0 --port ${PORT:-9000} --proxy-headers --forwarded-allow-ips='*'"]
