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

## Notes

- No business, layer, contract, or graph logic is included.
- `data/` is mounted read-only into the API container for future JSON/CSV data blocks.
- PostgreSQL uses the `pgvector/pgvector:pg16` image and enables the `vector` extension on first boot.

