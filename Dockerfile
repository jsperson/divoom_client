FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     DIVOOM_LAYOUT=config/layouts/dashboard.json     DIVOOM_PORT=8080

WORKDIR /app

RUN apt-get update     && apt-get install -y --no-install-recommends curl     && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN python -m pip install --no-cache-dir --upgrade pip     && python -m pip install --no-cache-dir .

EXPOSE 8080
VOLUME ["/app/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3   CMD curl -fsS "http://127.0.0.1:${DIVOOM_PORT}/api/status" >/dev/null || exit 1

CMD ["sh", "-c", "divoom serve ${DIVOOM_LAYOUT} --web --port ${DIVOOM_PORT} --no-device"]
