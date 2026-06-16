# cortex-api

Pure REST API skeleton for the Cortex shipment readiness service.

## Run with Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

The API is exposed on `API_PORT` from `.env`.

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"alive"}
```

## Run tests

```bash
pip install -r requirements.txt
pytest
```

## Local LLM config debug

When checking model settings inside Docker with a heredoc, use `exec -T` so stdin
is passed correctly. Key values must stay redacted.

```bash
docker compose exec -T api python - <<'PY'
from app.config import settings

for name in type(settings).model_fields:
    if "model" in name.lower() or "google" in name.lower() or "gemini" in name.lower() or "llm" in name.lower():
        try:
            value = getattr(settings, name)
            if "key" in name.lower() and value:
                value = "***REDACTED***"
            print(name, "=", value)
        except Exception as e:
            print(name, "=", type(e).__name__, e)
PY
```

## Notes

- No business, layer, contract, or graph logic is included.
- `data/` is mounted read-only into the API container for future JSON/CSV data blocks.
- PostgreSQL uses the `pgvector/pgvector:pg16` image and enables the `vector` extension on first boot.
