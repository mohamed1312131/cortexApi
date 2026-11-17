from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import NamedTuple

import pytest
from fastapi import Request

from app.repositories.agent_run_repository import InMemoryAgentRunRepository
from app.services.tracing import agent_run_recorder


@pytest.fixture
def agent_run_repo() -> InMemoryAgentRunRepository:
    repo = InMemoryAgentRunRepository()
    agent_run_recorder.set_repository(repo)
    return repo


@pytest.fixture(autouse=True)
def _use_in_memory_agent_run_repo(agent_run_repo: InMemoryAgentRunRepository) -> None:
    return None


class PinnedSettings(NamedTuple):
    """What ``pinned_settings`` pins. Test-only values, not secrets."""

    app_env: str = "test"
    api_key: str = "test-key-pinned-by-conftest-not-a-real-secret"
    tenant_id: str = "tenant-test"

    @property
    def api_keys_json(self) -> str:
        key_hash = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()
        return json.dumps({key_hash: self.tenant_id})


@pytest.fixture(autouse=True)
def pinned_settings(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[PinnedSettings]:
    """Pin the settings for every test, so a developer's ``.env`` never changes a result.

    * Environment: ``APP_ENV=test``, one known API key, auth not disabled,
      tracing off.
    * No ``.env`` file is read: not at import (``app.config`` reads none inside
      a pytest session, which covers the objects built at import time) and not
      while the test runs. The cached settings object is dropped and rebuilt
      from the pinned environment and the code defaults. The shared
      ``app.config.settings`` object (imported by reference all over the
      application) gets every value of the rebuilt object, and gets its own
      values back after the test.
    * Auth is ON (``APP_ENV=test`` + a key). The existing tests call ``/api/v1``
      without a key, so the API-key dependency of the application is replaced by
      one that answers with the pinned tenant, the way FastAPI documents it
      (https://fastapi.tiangolo.com/advanced/testing-dependencies/). Tests
      marked ``real_auth`` keep the real dependency.

    Tests marked ``live_llm`` need the developer's real provider settings: for
    them the ``.env`` file is still read, with the same values pinned on top.
    """
    from app import config
    from app.core import auth
    from app.main import app

    pinned = PinnedSettings()
    monkeypatch.setenv("APP_ENV", pinned.app_env)
    monkeypatch.setenv("CORTEX_API_KEYS_JSON", pinned.api_keys_json)
    monkeypatch.setenv("CORTEX_AUTH_DISABLED", "false")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    is_live = request.node.get_closest_marker("live_llm") is not None
    monkeypatch.setitem(
        config.Settings.model_config, "env_file", config.ENV_FILE if is_live else None
    )

    config.get_settings.cache_clear()
    auth._resolve_auth.cache_clear()
    rebuilt = config.get_settings()
    for name in config.Settings.model_fields:
        monkeypatch.setattr(config.settings, name, getattr(rebuilt, name))

    if request.node.get_closest_marker("real_auth") is None:

        async def _pinned_tenant(http_request: Request) -> str:
            http_request.state.tenant_id = pinned.tenant_id
            return pinned.tenant_id

        monkeypatch.setitem(app.dependency_overrides, auth.require_api_key, _pinned_tenant)

    yield pinned

    config.get_settings.cache_clear()
    auth._resolve_auth.cache_clear()
