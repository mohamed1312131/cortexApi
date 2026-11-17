"""W0-FU step 3: the settings are pinned for every test.

``tests/conftest.py::pinned_settings`` (autouse) pins APP_ENV=test, one known
API key and tracing off, reads no ``.env`` file and rebuilds the cached settings
object, so a developer's ``.env`` cannot change a test result.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app import config
from app.api.v1 import routes_agent_runs
from app.config import Settings, settings
from app.core import auth, tracing
from app.core.auth import API_KEY_HEADER_NAME, hash_api_key, require_api_key
from app.core.data_paths import DATA_DIR_ENV, PACKAGE_DATA_ROOT
from app.main import app
from app.repositories.agent_run_repository import InMemoryAgentRunRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_RUNS_URL = "/api/v1/cortex/cases/case-isolation-test/agent-runs"


@pytest.fixture
def agent_runs_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent-runs route answers 200 without a database."""
    monkeypatch.setattr(routes_agent_runs, "_REPOSITORY", InMemoryAgentRunRepository())


# --------------------------------------------------------------------------- #
# what is pinned
# --------------------------------------------------------------------------- #
def test_app_env_keys_and_tracing_are_pinned(pinned_settings):
    assert settings.app_env == "test" == pinned_settings.app_env
    assert settings.is_local is False
    assert settings.cortex_auth_disabled is False
    assert settings.cortex_api_keys_json == pinned_settings.api_keys_json
    assert settings.langsmith_tracing is False

    assert os.environ["APP_ENV"] == "test"
    assert os.environ["CORTEX_AUTH_DISABLED"] == "false"
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


def test_auth_is_on_with_exactly_the_pinned_key(pinned_settings):
    resolved = auth.auth_config()

    assert resolved.enabled is True
    assert dict(resolved.keys) == {hash_api_key(pinned_settings.api_key): pinned_settings.tenant_id}


def test_tracing_stays_off_when_the_app_starts():
    with TestClient(app):
        assert tracing._client is None
        assert os.environ["LANGSMITH_TRACING"] == "false"
        assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


# --------------------------------------------------------------------------- #
# no .env file, no stale cached object
# --------------------------------------------------------------------------- #
def test_no_env_file_is_read_during_a_test(tmp_path, monkeypatch):
    hostile = tmp_path / ".env"
    hostile.write_text("APP_ENV=production\nAPI_PORT=9471\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # a .env in the working directory is not read either

    assert Settings.model_config["env_file"] is None
    assert Settings().api_port == Settings(_env_file=None).api_port != 9471
    # The file itself is readable: it is ignored, not missing.
    assert Settings(_env_file=hostile).api_port == 9471


def test_the_cached_settings_object_is_rebuilt_from_the_pins():
    cached = config.get_settings()

    assert cached is config.get_settings()
    assert cached.app_env == "test"
    assert cached.langsmith_tracing is False


def test_every_field_of_the_shared_settings_object_is_pinned():
    """Not only the three named values: no field keeps a value from a .env file."""
    without_env_file = Settings(_env_file=None)

    differing = [
        name
        for name in Settings.model_fields
        if getattr(settings, name) != getattr(without_env_file, name)
    ]
    assert differing == []


def test_a_change_made_by_a_test_does_not_reach_the_next_test_part_1(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "cortex_auth_disabled", True)

    assert auth.auth_config().enabled is False


def test_a_change_made_by_a_test_does_not_reach_the_next_test_part_2():
    assert settings.app_env == "test"
    assert settings.cortex_auth_disabled is False
    assert auth.auth_config().enabled is True


# --------------------------------------------------------------------------- #
# the auth dependency of the application in tests
# --------------------------------------------------------------------------- #
def test_api_v1_answers_without_a_key_through_the_test_dependency(agent_runs_offline):
    """What lets the tests written before W0-FU call /api/v1 with auth on."""
    assert require_api_key in app.dependency_overrides

    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL)

    assert response.status_code == 200


def test_the_test_dependency_answers_with_the_pinned_tenant(pinned_settings):
    probe = FastAPI()

    @probe.get("/api/v1/whoami")
    async def whoami(request: Request, tenant_id: str = Depends(require_api_key)) -> dict:
        return {"state": request.state.tenant_id, "returned": tenant_id}

    probe.dependency_overrides.update(app.dependency_overrides)

    response = TestClient(probe).get("/api/v1/whoami")

    assert response.json() == {
        "state": pinned_settings.tenant_id,
        "returned": pinned_settings.tenant_id,
    }


@pytest.mark.real_auth
def test_real_auth_mark_keeps_the_real_dependency(agent_runs_offline, pinned_settings):
    assert require_api_key not in app.dependency_overrides

    with TestClient(app) as client:
        missing = client.get(AGENT_RUNS_URL)
        wrong = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: "not-the-pinned-key"})
        valid = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: pinned_settings.api_key})

    assert (missing.status_code, wrong.status_code, valid.status_code) == (401, 403, 200)


def test_the_test_dependency_is_removed_after_each_test_part_1():
    assert require_api_key in app.dependency_overrides


@pytest.mark.real_auth
def test_the_test_dependency_is_removed_after_each_test_part_2():
    assert app.dependency_overrides == {}


# --------------------------------------------------------------------------- #
# a developer's .env does not change a result (run for real, in a throwaway copy)
# --------------------------------------------------------------------------- #
_SOME_KEY_HASH = hash_api_key("developer-key-not-a-real-secret")

_ENV_FILES = {
    "api_keys_set": f'CORTEX_API_KEYS_JSON={{"{_SOME_KEY_HASH}":"tenant-dev"}}\n',
    "app_env_production": "APP_ENV=production\n",
    "everything_different": (
        "APP_ENV=local\n"
        f'CORTEX_API_KEYS_JSON={{"{_SOME_KEY_HASH}":"tenant-dev"}}\n'
        "CORTEX_AUTH_DISABLED=true\n"
        # Tracing "on" with no key: if this were ever honoured the app would
        # refuse to start instead of sending anything to a third party.
        "LANGSMITH_TRACING=true\n"
        "LANGSMITH_API_KEY=\n"
        "LLM_PROVIDER=google\n"
        "CORTEX_REDIS_FALLBACK_ENABLED=false\n"
        "ORCHESTRATOR_CACHE_ENABLED=false\n"
        "DATABASE_URL=postgresql+asyncpg://dev:dev-pw@127.0.0.1:1/developer_db\n"
        "REDIS_URL=redis://127.0.0.1:1/7\n"
    ),
}

_INNER_TEST = '''
from fastapi.testclient import TestClient

from app import config
from app.api.v1 import routes_agent_runs
from app.config import Settings, settings
from app.main import app
from app.repositories.agent_run_repository import InMemoryAgentRunRepository


def test_the_env_file_of_this_checkout_is_there_and_would_be_read():
    assert config.ENV_FILE.is_file()
    assert Settings(_env_file=config.ENV_FILE).api_port == 9471


def test_objects_built_at_import_did_not_take_their_settings_from_it():
    """The engine, the Redis store and the app are built while their modules are
    imported, before any fixture runs."""
    from app.core.db import engine
    from app.services.layer1.case_state_manager import case_state_store

    defaults = Settings(_env_file=None)
    assert engine.url.render_as_string(hide_password=False) == defaults.database_url
    assert engine.url.host == "postgres"
    redis_target = case_state_store._redis.connection_pool.connection_kwargs
    assert (redis_target["host"], redis_target["port"], redis_target["db"]) == ("redis", 6379, 0)
    assert case_state_store._fallback_enabled is True
    assert app.docs_url is None and app.openapi_url is None  # APP_ENV=local in one scenario


def test_and_it_changes_nothing(monkeypatch):
    monkeypatch.setattr(routes_agent_runs, "_REPOSITORY", InMemoryAgentRunRepository())

    assert settings.api_port == 8100
    assert settings.app_env == "test"
    assert settings.cortex_auth_disabled is False
    assert settings.langsmith_tracing is False
    assert settings.llm_provider == "none"
    assert settings.cortex_redis_fallback_enabled is True
    assert settings.orchestrator_cache_enabled is True
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/cortex/cases/case-x/agent-runs").status_code == 200
'''


@pytest.mark.parametrize("scenario", sorted(_ENV_FILES))
def test_a_developer_env_file_does_not_change_test_results(scenario, tmp_path):
    """Copy the application and tests/conftest.py next to a synthetic .env and
    run a small test session there, with nothing pinned from the outside."""
    checkout = tmp_path / "checkout"
    shutil.copytree(REPO_ROOT / "app", checkout / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (checkout / "tests").mkdir()
    shutil.copy(REPO_ROOT / "tests" / "conftest.py", checkout / "tests" / "conftest.py")
    shutil.copy(REPO_ROOT / "pyproject.toml", checkout / "pyproject.toml")
    (checkout / "tests" / "test_inner.py").write_text(_INNER_TEST, encoding="utf-8")
    # API_PORT=9471 marks the file: the inner session proves it could read it.
    (checkout / ".env").write_text(_ENV_FILES[scenario] + "API_PORT=9471\n", encoding="utf-8")

    # Nothing of this session's pinned environment reaches the inner session.
    setting_names = {name.upper() for name in Settings.model_fields}
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in setting_names and key != "LANGCHAIN_TRACING_V2"
    }
    env[DATA_DIR_ENV] = str(PACKAGE_DATA_ROOT)  # the copy has no data folder of its own

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_inner.py"],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stdout[-3000:] + completed.stderr[-2000:]
    assert "3 passed" in completed.stdout
