"""Temporary API-key authentication (Wave 0).

Replaced in Wave 2 by the tenants table. Until then:

* clients send their key in the ``X-API-Key`` header;
* the server only knows sha256 hashes of the keys, each mapped to a tenant id
  (env ``CORTEX_API_KEYS_JSON`` = ``{"<sha256 hex>": "<tenant_id>"}``);
* hashes are compared with ``hmac.compare_digest``;
* the tenant id is put on ``request.state.tenant_id`` for later use.

Missing key -> 401. Unknown key -> 403.

When no key is configured the API refuses to start, except with ``APP_ENV=local``
where authentication is switched off (local development and the offline test
suite) and a warning is logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from functools import lru_cache

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

API_KEY_HEADER_NAME = "X-API-Key"
API_KEYS_ENV = "CORTEX_API_KEYS_JSON"
LOCAL_APP_ENV = "local"
LOCAL_DEV_TENANT_ID = "local-dev"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
    description="Cortex API key. Required on every /api/v1 route.",
)


class ApiKeyConfigError(RuntimeError):
    """``CORTEX_API_KEYS_JSON`` is missing (outside local) or malformed."""


def hash_api_key(raw_key: str) -> str:
    """Return the value to store in ``CORTEX_API_KEYS_JSON`` for a raw key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def parse_api_keys(raw_json: str) -> dict[str, str]:
    """Parse ``{"<sha256 hex>": "<tenant_id>"}``. Never echoes configured values."""
    text = raw_json.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiKeyConfigError(f"{API_KEYS_ENV} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ApiKeyConfigError(
            f'{API_KEYS_ENV} must be a JSON object {{"<sha256 hex>": "<tenant_id>"}}.'
        )
    keys: dict[str, str] = {}
    for position, (key_hash, tenant_id) in enumerate(payload.items(), start=1):
        normalized = str(key_hash).strip().lower()
        if not _SHA256_HEX_RE.fullmatch(normalized):
            raise ApiKeyConfigError(
                f"{API_KEYS_ENV} entry {position}: the key must be the sha256 hex digest "
                "(64 hex characters) of the API key, not the key itself."
            )
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ApiKeyConfigError(
                f"{API_KEYS_ENV} entry {position}: tenant_id must be a non-empty string."
            )
        keys[normalized] = tenant_id.strip()
    return keys


@lru_cache(maxsize=8)
def _resolve_api_keys(raw_json: str, app_env: str) -> dict[str, str]:
    keys = parse_api_keys(raw_json)
    if not keys and app_env.strip().lower() != LOCAL_APP_ENV:
        raise ApiKeyConfigError(
            f"{API_KEYS_ENV} is empty and APP_ENV={app_env!r}: refusing to serve "
            "/api/v1 without API keys. Configure at least one key hash."
        )
    return keys


def configured_api_keys() -> dict[str, str]:
    """Key hash -> tenant id. Empty only when APP_ENV=local and no key is set."""
    return _resolve_api_keys(settings.cortex_api_keys_json, settings.app_env)


def validate_api_key_config() -> None:
    """Fail fast at startup on a bad configuration; warn when auth is off."""
    keys = configured_api_keys()
    if keys:
        logger.info("auth.api_keys_configured count=%d", len(keys))
    else:
        logger.warning(
            "auth.disabled API-key authentication is OFF (no %s, APP_ENV=local). "
            "Never run a shared or production deployment like this.",
            API_KEYS_ENV,
        )


def match_tenant(presented_key: str, keys: dict[str, str]) -> str | None:
    """Return the tenant id for a presented raw key, or None."""
    presented_hash = hash_api_key(presented_key)
    tenant_id: str | None = None
    # Compare against every configured hash (no early exit) in constant time.
    for known_hash, known_tenant in keys.items():
        if hmac.compare_digest(presented_hash, known_hash):
            tenant_id = known_tenant
    return tenant_id


async def require_api_key(
    request: Request,
    presented_key: str | None = Security(api_key_header),
) -> str:
    """FastAPI dependency for every ``/api/v1`` route. Returns the tenant id."""
    keys = configured_api_keys()
    if not keys:
        request.state.tenant_id = LOCAL_DEV_TENANT_ID
        return LOCAL_DEV_TENANT_ID

    if not presented_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    tenant_id = match_tenant(presented_key, keys)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    request.state.tenant_id = tenant_id
    return tenant_id
