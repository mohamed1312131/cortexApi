#!/bin/sh
# Container start: check, migrate, serve. Any failing step stops the container.
set -eu

# 1. Check the configuration and the data files once, before any worker starts.
#    With several workers uvicorn restarts a worker whose startup fails, forever;
#    failing here stops the container with one clear error instead.
echo "entrypoint: preflight (API-key configuration, Layer 2 data)"
python -m app.core.preflight

# 2. Bring the database schema up to date. `alembic upgrade head` creates the
#    agent_runs table on a fresh database and is a no-op when the schema is
#    already current. If it fails (database unreachable, bad migration) the
#    container stops here instead of serving on a wrong schema.
echo "entrypoint: alembic upgrade head"
alembic upgrade head

# 3. Serve.
echo "entrypoint: starting uvicorn on port ${API_PORT:-8100} with ${CORTEX_API_WORKERS:-1} worker(s)"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8100}" \
    --workers "${CORTEX_API_WORKERS:-1}"
