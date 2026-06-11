FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# CORTEX_API_WORKERS controls the number of worker processes (default 1 for
# local dev). docker-compose sets it higher for a production-like runtime.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${API_PORT:-8000} --workers ${CORTEX_API_WORKERS:-1}"]

