FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

# uv installs the exact versions pinned in uv.lock (same tool and lock as local dev and CI).
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# The entrypoint runs `alembic upgrade head`, then uvicorn on API_PORT (default
# 8100; the legacy Backend keeps 8000). CORTEX_API_WORKERS controls the number of
# worker processes (default 1; docker-compose sets it higher).
CMD ["sh", "/app/docker/entrypoint.sh"]
