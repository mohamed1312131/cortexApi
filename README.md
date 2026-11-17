# cortex-api

REST API of the Cortex shipment-readiness engine (FastAPI + LangGraph + LangChain).

A shipment message goes through four layers:

| Layer | What it does | Uses an LLM |
|---|---|---|
| L1 Intake | Turns the user message into a `ValidatedShipmentRequest` | yes |
| L2 Facts | Builds a deterministic `FactPackage` from the data connectors (air, sea, road) | no |
| L3 Reasoning | Deterministic decision engine, Analyst and optional Critic, Python safety gate | yes (Analyst, Critic) |
| L4 Report | Builds the operational evidence and writes the report | yes |

Design documents are in `docs/` (one architecture file per layer).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness: the process answers. Open. |
| GET | `/ready` | Readiness: database reachable, Redis reachable, Layer 2 data loaded. 200 or 503. Open. |
| POST | `/api/v1/cortex/full-message` | Full run L1 → L2 → L3 → L4 |
| POST | `/api/v1/cortex/message` | L1, then L2 when the request is ready |
| GET | `/api/v1/cortex/artifacts/{artifact}` | Full cached artifact of a run (`layer2`, `layer3`, `layer4`), for debugging |
| GET | `/api/v1/cortex/cases/{case_id}/agent-runs` | Agent run trace of a case |
| POST | `/api/v1/intake/message` | L1 only |
| POST | `/api/v1/layer3/reason` | L3 only (takes a `FactPackage`) |
| POST | `/api/v1/layer4/report` | L4 only |

Every `/api/v1/*` route requires an API key (see below).

## Setup

Dependencies are pinned in `pyproject.toml` and locked in `uv.lock` (Python 3.12).
Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --frozen          # creates .venv with the exact locked versions
cp .env.example .env      # then fill in what you need; .env is never committed
```

To change a dependency: edit the pin in `pyproject.toml`, run `uv lock`, commit both files.

## Run the tests

```bash
uv run python -m pytest -q -m "not live_llm"
```

The offline suite needs no database, no Redis and no LLM key. CI
(`.github/workflows/ci.yml`) runs the same command on every push and pull request.

## Run with Docker Compose

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:8100/health    # {"status":"alive"}
curl http://localhost:8100/ready     # {"status":"ready","checks":{...}}
```

Host ports (override in `.env` or the shell):

| Variable | Default | Note |
|---|---|---|
| `API_PORT` | 8100 | the legacy Backend keeps 8000 |
| `POSTGRES_HOST_PORT` | 5433 | avoids a local Postgres on 5432 |
| `REDIS_HOST_PORT` | 6380 | avoids a local Redis on 6379 |

On start the API container runs `alembic upgrade head` (creates the `agent_runs`
table), loads the Layer 2 data, then starts uvicorn. `data/` is mounted read-only.

## Run on the host

```bash
docker compose up -d postgres redis
DATABASE_URL=postgresql+asyncpg://cortex:cortex@localhost:5433/cortex \
REDIS_URL=redis://localhost:6380/0 \
  uv run sh -c 'alembic upgrade head && uvicorn app.main:app --port 8100'
```

## Layer 2 data

- The data root is `CORTEX_DATA_DIR` if set, otherwise the repository's `data/`
  folder resolved from the code location. The working directory does not matter.
- The catalogue and the data files the connectors use are read once at startup
  and shared read-only; connectors do not read the disk per request.
- If a required data file is missing or is not valid JSON, the API refuses to
  start and the error names the file. Missing data is never reported as "clear".

## API keys (temporary)

Replaced by the tenants table in Wave 2. Until then:

- Clients send the key in the `X-API-Key` header. Missing key → 401, unknown key → 403.
- The server only stores sha256 hashes, each mapped to a tenant id, in
  `CORTEX_API_KEYS_JSON`:

  ```bash
  python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" 'the-key'
  # CORTEX_API_KEYS_JSON={"<that hash>":"tenant-a"}
  ```

- With no key configured, the API refuses to start, except when `APP_ENV=local`:
  authentication is then **off** and a warning is logged. Use that for local
  development only.

## LangSmith tracing

Off by default (`LANGSMITH_TRACING=false`). Turning it on sends prompts and model
outputs to a third party; enable it deliberately.

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

- PostgreSQL uses the `pgvector/pgvector:pg16` image and enables the `vector` extension on first boot.
