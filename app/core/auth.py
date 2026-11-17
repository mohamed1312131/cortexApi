"""Temporary API-key authentication (Wave 0).

Replaced in Wave 2 by the tenants table. Until then:

* clients send their key in the ``X-API-Key`` header;
* the server only knows sha256 hashes of the keys, each mapped to a tenant id
  (env ``CORTEX_API_KEYS_JSON`` = ``{"<sha256 hex>": "<tenant_id>"}``);
* hashes are compared with ``hmac.compare_digest``;
* the tenant id is put on ``request.state.tenant_id`` for later use.

Missing key -> 401. Unknown key -> 403.

Authentication fails closed. It is ON unless ``CORTEX_AUTH_DISABLED=true`` and
``APP_ENV=local`` are both set explicitly (``APP_ENV`` is not ``local`` by
default):

=================  ==========  ======================  =========================
APP_ENV            keys        CORTEX_AUTH_DISABLED    result
=================  ==========  ======================  =========================
anything           configured  false                   auth on
not ``local``      none        false                   refuses to start
not ``local``      any         true                    refuses to start
``local``          none        false                   starts; /api/v1 refuses
                                                       every request (401/403)
``local``          any         true                    auth off, warning logged
=================  ==========  ======================  =========================
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from app.config import ENV_FILE, LOCAL_APP_ENV, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

API_KEY_HEADER_NAME = "X-API-Key"
API_KEYS_ENV = "CORTEX_API_KEYS_JSON"
AUTH_DISABLED_ENV = "CORTEX_AUTH_DISABLED"
LOCAL_DEV_TENANT_ID = "local-dev"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
    description="Cortex API key. Required on every /api/v1 route.",
)


class ApiKeyConfigError(RuntimeError):
    """The auth configuration is unusable: no key outside local, a malformed
    ``CORTEX_API_KEYS_JSON``, or ``CORTEX_AUTH_DISABLED`` outside local."""


@dataclass(frozen=True)
class AuthConfig:
    """The resolved auth configuration. ``enabled`` is False only for an
    explicit ``CORTEX_AUTH_DISABLED=true`` together with ``APP_ENV=local``."""

    enabled: bool
    # key hash -> tenant id (read-only). May be empty while ``enabled`` is True:
    # then no request can pass.
    keys: Mapping[str, str]


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


def _settings_file_hint() -> str:
    """Where the settings file is looked for, for error messages (path only)."""
    state = "found" if ENV_FILE.is_file() else "not found"
    return f"Settings file {ENV_FILE}: {state}."


@lru_cache(maxsize=8)
def _resolve_auth(raw_json: str, app_env: str, auth_disabled: bool) -> AuthConfig:
    is_local = app_env.strip().lower() == LOCAL_APP_ENV
    if auth_disabled:
        if not is_local:
            raise ApiKeyConfigError(
                f"{AUTH_DISABLED_ENV}=true is only accepted together with an explicit "
                f"APP_ENV={LOCAL_APP_ENV} (APP_ENV is {app_env!r}): refusing to start. "
                f"Remove {AUTH_DISABLED_ENV} or set it to false. {_settings_file_hint()}"
            )
        # Explicitly off for local development: the keys are not used, so they
        # are not read either (the .env.example placeholder may still be there).
        return AuthConfig(enabled=False, keys=MappingProxyType({}))
    keys = MappingProxyType(parse_api_keys(raw_json))
    if not keys and not is_local:
        raise ApiKeyConfigError(
            f"{API_KEYS_ENV} is empty and APP_ENV={app_env!r} (APP_ENV is not "
            f"{LOCAL_APP_ENV!r} unless it is set explicitly): refusing to serve /api/v1 "
            f"without API keys. Configure at least one key hash. {_settings_file_hint()}"
        )
    return AuthConfig(enabled=True, keys=keys)


def auth_config() -> AuthConfig:
    """Resolve the auth configuration from the settings. Raises ``ApiKeyConfigError``."""
    return _resolve_auth(
        settings.cortex_api_keys_json,
        settings.app_env,
        bool(settings.cortex_auth_disabled),
    )


def configured_api_keys() -> Mapping[str, str]:
    """Key hash -> tenant id (read-only). Empty when no key is set or auth is off."""
    return auth_config().keys


def validate_api_key_config() -> None:
    """Fail fast at startup on a bad configuration; warn when nothing can pass
    or when auth is off."""
    config = auth_config()
    if not config.enabled:
        logger.warning(
            "auth.disabled API-key authentication is OFF (%s=true, APP_ENV=%s). "
            "Never run a shared or production deployment like this.",
            AUTH_DISABLED_ENV,
            LOCAL_APP_ENV,
        )
    elif config.keys:
        logger.info("auth.api_keys_configured count=%d", len(config.keys))
    else:
        logger.warning(
            "auth.no_api_keys APP_ENV=%s and %s is empty: authentication is ON and "
            "every /api/v1 request will be refused (401/403). Set %s, or set "
            "%s=true to switch authentication off for local development.",
            LOCAL_APP_ENV,
            API_KEYS_ENV,
            API_KEYS_ENV,
            AUTH_DISABLED_ENV,
        )


def match_tenant(presented_key: str, keys: Mapping[str, str]) -> str | None:
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
    config = auth_config()
    if not config.enabled:
        request.state.tenant_id = LOCAL_DEV_TENANT_ID
        return LOCAL_DEV_TENANT_ID

    if not presented_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    tenant_id = match_tenant(presented_key, config.keys)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    request.state.tenant_id = tenant_id
    return tenant_id
