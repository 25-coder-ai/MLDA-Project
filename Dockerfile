# Multi-stage image for the TRUST-CODE API, Celery worker and sandbox runner.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git patch && rm -rf /var/lib/apt/lists/*
WORKDIR /app

FROM base AS deps
COPY requirements.txt .
RUN pip install -r requirements.txt semgrep celery redis docker

FROM deps AS app
COPY config ./config
COPY src ./src
COPY examples ./examples
COPY tests ./tests
COPY pyproject.toml README.md ./
RUN useradd --create-home --uid 10001 trust && chown -R trust /app
USER trust
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
