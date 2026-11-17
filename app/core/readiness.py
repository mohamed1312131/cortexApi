"""Readiness endpoint: ``GET /ready``.

``/health`` (liveness) only says the process answers. ``/ready`` says the process
can do its job: the database is reachable, Redis is reachable, and the Layer 2
data is loaded. It returns 200 when all three hold, otherwise 503 with the
failing checks named. It is open (no API key) so orchestrators can probe it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import settings
from app.core.db import engine
from app.core.logging import get_logger
from app.services.layer2.data_catalog import data_load_error, is_data_loaded

logger = get_logger(__name__)

router = APIRouter()

CHECK_TIMEOUT_SECONDS = 2.0


async def check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis() -> None:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=CHECK_TIMEOUT_SECONDS,
        socket_timeout=CHECK_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def check_data() -> None:
    if not is_data_loaded():
        raise RuntimeError(data_load_error() or "data not loaded")


async def _run_check(name: str, check: Callable[[], Awaitable[None]]) -> str:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # a failed probe must never crash the endpoint
        # Details go to the server log only; the response names the failure type.
        logger.warning("ready.check_failed check=%s error=%s: %s", name, type(exc).__name__, exc)
        if isinstance(exc, TimeoutError):
            return "timeout"
        return f"error: {type(exc).__name__}"
    return "ok"


@router.get("/ready")
async def ready() -> JSONResponse:
    names = ("database", "redis", "data")
    results = await asyncio.gather(
        _run_check("database", check_database),
        _run_check("redis", check_redis),
        _run_check("data", check_data),
    )
    checks = dict(zip(names, results, strict=True))
    is_ready = all(result == "ok" for result in results)
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
    )
