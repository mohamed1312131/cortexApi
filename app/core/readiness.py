"""Readiness endpoint: ``GET /ready``.

``/health`` (liveness) only says the process answers. ``/ready`` says the process
can do its job: the database is reachable, Redis is reachable, and the Layer 2
data is loaded. It returns 200 when all three hold, otherwise 503 with the
failing checks named. It is open (no API key) so orchestrators can probe it.

Because it is open, the response never carries an exception message. The full
detail of a failed check (type, message, cause chain, traceback) goes to the
server log only, with credentials masked (``redact_credentials``).
"""

from __future__ import annotations

import asyncio
import re
import traceback
from collections.abc import Awaitable, Callable
from urllib.parse import unquote, urlsplit

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

REDACTED = "***"
# "scheme://user:password@host" or "scheme://user@host": the part before "@".
_URL_USERINFO_RE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/\s@]+@")
# "password=secret", "password: 'two words'", '"password": "secret"' as found in
# key=value connection strings and dumped connection parameters.
_PASSWORD_PAIR_RE = re.compile(
    r"""(?P<key>\b(?:password|passwd|pwd)\b['"]?\s*[=:]\s*)(?:'[^']*'|"[^"]*"|\S+)""",
    re.IGNORECASE,
)


def _configured_passwords() -> list[str]:
    """The passwords inside DATABASE_URL and REDIS_URL, as written and decoded."""
    found: set[str] = set()
    for url in (settings.database_url, settings.redis_url):
        try:
            password = urlsplit(url).password
        except ValueError:
            continue
        if password:
            found.update({password, unquote(password)})
    # Longest first, so a password that contains another one is masked whole.
    return sorted(found, key=len, reverse=True)


def redact_credentials(text: str) -> str:
    """Mask credentials in a text that is about to be logged.

    The asyncpg, SQLAlchemy and redis messages seen for a failed connection name
    the host, port, user and database, not the password. That is not a
    guarantee for every driver path, so three things are masked anyway: the
    user:password part of any URL, ``password=...`` pairs, and every literal
    occurrence of the configured database and Redis passwords. (A password
    that is also an ordinary word is masked wherever that word appears.)
    """
    text = _URL_USERINFO_RE.sub(rf"\g<scheme>{REDACTED}@", text)
    text = _PASSWORD_PAIR_RE.sub(rf"\g<key>{REDACTED}", text)
    for password in _configured_passwords():
        text = text.replace(password, REDACTED)
    return text


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
        # The full detail goes to the server log only, credentials masked. The
        # response carries a short code and never the exception message.
        detail = redact_credentials("".join(traceback.format_exception(exc)))
        logger.warning(
            "ready.check_failed check=%s error=%s detail:\n%s",
            name,
            type(exc).__name__,
            detail,
        )
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
