"""Temporary API-key auth (Wave 0): X-API-Key on every /api/v1 route.

Missing key -> 401, wrong key -> 403, valid key -> 200. /health and /ready stay open.

Auth fails closed (W0-FU): it is on unless CORTEX_AUTH_DISABLED=true and
APP_ENV=local are both set explicitly. See the matrix at the end of this file.

Every test here runs against the real API-key dependency (``real_auth``): the
replacement installed by tests/conftest.py::pinned_settings is not used.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.v1 import routes_agent_runs
from app.config import Settings, settings
from app.core import auth, readiness
from app.core.auth import (
    API_KEY_HEADER_NAME,
    API_KEYS_ENV,
    AUTH_DISABLED_ENV,
    LOCAL_DEV_TENANT_ID,
    ApiKeyConfigError,
    hash_api_key,
    parse_api_keys,
    require_api_key,
)
from app.main import API_V1_PREFIX, app
from app.repositories.agent_run_repository import InMemoryAgentRunRepository

pytestmark = pytest.mark.real_auth

# Test-only keys. They exist nowhere outside this file.
ALPHA_KEY = "test-key-alpha-not-a-real-secret"
BETA_KEY = "test-key-beta-not-a-real-secret"
WRONG_KEY = "test-key-that-is-not-configured"

AGENT_RUNS_URL = "/api/v1/cortex/cases/case-auth-test/agent-runs"


def _keys_json(mapping: dict[str, str]) -> str:
    return json.dumps({hash_api_key(raw): tenant for raw, tenant in mapping.items()})


@pytest.fixture
def keys_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "cortex_api_keys_json",
        _keys_json({ALPHA_KEY: "tenant-alpha", BETA_KEY: "tenant-beta"}),
    )


@pytest.fixture
def agent_runs_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent-runs route answers 200 without a database."""
    monkeypatch.setattr(routes_agent_runs, "_REPOSITORY", InMemoryAgentRunRepository())


def _api_v1_routes() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith(API_V1_PREFIX):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                found.append((method, route.path))
    return sorted(found)


# --------------------------------------------------------------------------- #
# the three required cases, on a real /api/v1 route
# --------------------------------------------------------------------------- #
def test_missing_key_is_401(keys_configured, agent_runs_offline):
    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL)

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing API key."}
    assert response.headers["WWW-Authenticate"] == "APIKey"


def test_empty_key_is_401(keys_configured, agent_runs_offline):
    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: ""})

    assert response.status_code == 401


def test_wrong_key_is_403(keys_configured, agent_runs_offline):
    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: WRONG_KEY})

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid API key."}


def test_valid_key_is_200(keys_configured, agent_runs_offline):
    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: ALPHA_KEY})

    assert response.status_code == 200
    assert response.json() == {"case_id": "case-auth-test", "runs": []}


def test_stored_hash_is_not_accepted_as_a_key(keys_configured, agent_runs_offline):
    """Knowing the configured hash must not be enough to get in."""
    with TestClient(app) as client:
        response = client.get(
            AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: hash_api_key(ALPHA_KEY)}
        )

    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# every /api/v1 route is protected; /health and /ready are not
# --------------------------------------------------------------------------- #
def test_the_app_has_api_v1_routes_to_protect():
    routes = _api_v1_routes()

    assert ("POST", "/api/v1/cortex/full-message") in routes
    assert ("POST", "/api/v1/intake/message") in routes
    assert ("POST", "/api/v1/layer3/reason") in routes
    assert ("POST", "/api/v1/layer4/report") in routes
    assert len(routes) >= 7


@pytest.mark.parametrize(("method", "path"), _api_v1_routes())
def test_every_api_v1_route_rejects_a_missing_or_wrong_key(method, path, keys_configured):
    url = path.replace("{case_id}", "case-x").replace("{artifact}", "layer2")
    with TestClient(app) as client:
        missing = client.request(method, url)
        wrong = client.request(method, url, headers={API_KEY_HEADER_NAME: WRONG_KEY})

    # The key is checked before the body: no valid payload is needed to be refused.
    assert missing.status_code == 401, (method, path)
    assert wrong.status_code == 403, (method, path)


def test_every_route_outside_api_v1_is_a_known_open_route():
    """A new route must either live under /api/v1 (protected) or be listed here."""
    open_paths = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and not route.path.startswith(API_V1_PREFIX)
    }

    assert open_paths == {"/health", "/ready"}


def test_health_and_ready_stay_open(keys_configured, monkeypatch):
    async def _ok() -> None:
        return None

    monkeypatch.setattr(readiness, "check_database", _ok)
    monkeypatch.setattr(readiness, "check_redis", _ok)

    with TestClient(app) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


# --------------------------------------------------------------------------- #
# tenant on the request state
# --------------------------------------------------------------------------- #
def _probe_app() -> FastAPI:
    probe = FastAPI()

    @probe.get("/api/v1/whoami")
    async def whoami(request: Request, tenant_id: str = Depends(require_api_key)) -> dict:
        return {"state": request.state.tenant_id, "returned": tenant_id}

    return probe


@pytest.mark.parametrize(
    ("key", "tenant"), [(ALPHA_KEY, "tenant-alpha"), (BETA_KEY, "tenant-beta")]
)
def test_tenant_id_is_attached_to_the_request_state(key, tenant, keys_configured):
    response = TestClient(_probe_app()).get(
        "/api/v1/whoami", headers={API_KEY_HEADER_NAME: key}
    )

    assert response.status_code == 200
    assert response.json() == {"state": tenant, "returned": tenant}


def test_hashes_are_compared_with_hmac_compare_digest(keys_configured, monkeypatch):
    calls: list[tuple[str, str]] = []
    real_compare = auth.hmac.compare_digest

    def _spy(left, right):
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", _spy)

    response = TestClient(_probe_app()).get(
        "/api/v1/whoami", headers={API_KEY_HEADER_NAME: BETA_KEY}
    )

    assert response.status_code == 200
    presented_hash = hashlib.sha256(BETA_KEY.encode("utf-8")).hexdigest()
    # Every configured hash is compared (no early exit), always against the
    # sha256 of the presented key, never against the raw key.
    assert len(calls) == 2
    assert {left for left, _ in calls} == {presented_hash}
    assert {right for _, right in calls} == {hash_api_key(ALPHA_KEY), hash_api_key(BETA_KEY)}


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_keys_json_holds_hashes_mapped_to_tenants():
    parsed = parse_api_keys(_keys_json({ALPHA_KEY: "tenant-alpha"}))

    assert parsed == {hashlib.sha256(ALPHA_KEY.encode("utf-8")).hexdigest(): "tenant-alpha"}
    assert parse_api_keys("") == {}
    assert parse_api_keys("   ") == {}


@pytest.mark.parametrize(
    "raw_json",
    [
        "{not json",
        '["a-list-is-not-a-mapping"]',
        json.dumps({ALPHA_KEY: "tenant-alpha"}),  # a raw key instead of its hash
        json.dumps({hash_api_key(ALPHA_KEY): ""}),  # empty tenant
        json.dumps({hash_api_key(ALPHA_KEY): 7}),  # tenant is not a string
    ],
)
def test_bad_keys_json_is_refused_without_echoing_it(raw_json):
    with pytest.raises(ApiKeyConfigError) as excinfo:
        parse_api_keys(raw_json)

    assert ALPHA_KEY not in str(excinfo.value)
    assert hash_api_key(ALPHA_KEY) not in str(excinfo.value)


def test_no_keys_outside_local_refuses_to_start(monkeypatch):
    monkeypatch.setattr(settings, "cortex_api_keys_json", "")
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(ApiKeyConfigError):
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line


def test_no_keys_outside_local_never_serves_api_v1(monkeypatch, agent_runs_offline):
    """Even if startup validation were bypassed, a request is not let through."""
    monkeypatch.setattr(settings, "cortex_api_keys_json", "")
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(ApiKeyConfigError):
        TestClient(app).get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: ALPHA_KEY})


def test_bad_keys_json_refuses_to_start(monkeypatch):
    monkeypatch.setattr(settings, "cortex_api_keys_json", "{not json")

    with pytest.raises(ApiKeyConfigError):
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line


# --------------------------------------------------------------------------- #
# fail-closed matrix (W0-FU): auth is on unless CORTEX_AUTH_DISABLED=true and
# APP_ENV=local are both explicit
# --------------------------------------------------------------------------- #
ENV_EXAMPLE_PLACEHOLDER = '{"PASTE_SHA256_HEX_OF_YOUR_API_KEY_HERE":"tenant-local"}'


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_env: str,
    keys_json: str = "",
    auth_disabled: bool = False,
) -> None:
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "cortex_api_keys_json", keys_json)
    monkeypatch.setattr(settings, "cortex_auth_disabled", auth_disabled)


def test_local_without_keys_starts_and_refuses_every_request(
    monkeypatch, agent_runs_offline, pinned_settings
):
    """local + no keys + no explicit disable: 401, not open (was 200 before W0-FU)."""
    _configure(monkeypatch, app_env="local")

    with TestClient(app) as client:  # the process starts
        no_key = client.get(AGENT_RUNS_URL)
        some_key = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: ALPHA_KEY})
        pinned_key = client.get(
            AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: pinned_settings.api_key}
        )

    assert no_key.status_code == 401
    assert no_key.json() == {"detail": "Missing API key."}
    # No key is configured, so no key can be the right one.
    assert some_key.status_code == 403
    assert pinned_key.status_code == 403


@pytest.mark.parametrize(("method", "path"), _api_v1_routes())
def test_local_without_keys_refuses_every_api_v1_route(method, path, monkeypatch):
    _configure(monkeypatch, app_env="local")
    url = path.replace("{case_id}", "case-x").replace("{artifact}", "layer2")

    with TestClient(app) as client:
        response = client.request(method, url)

    assert response.status_code == 401, (method, path)


def test_local_without_keys_logs_that_nothing_can_pass(monkeypatch, caplog):
    _configure(monkeypatch, app_env="local")

    with caplog.at_level("WARNING", logger="cortex"):
        auth.validate_api_key_config()

    assert "auth.no_api_keys" in caplog.text
    assert "auth.disabled" not in caplog.text


def test_local_with_explicit_disable_is_open(monkeypatch, agent_runs_offline, caplog):
    """local + CORTEX_AUTH_DISABLED=true: the only configuration without auth."""
    _configure(monkeypatch, app_env="local", auth_disabled=True)

    with caplog.at_level("WARNING", logger="cortex"):
        with TestClient(app) as client:
            response = client.get(AGENT_RUNS_URL)

    assert response.status_code == 200
    assert response.json() == {"case_id": "case-auth-test", "runs": []}
    assert "auth.disabled" in caplog.text  # never silently


def test_local_with_explicit_disable_uses_the_local_dev_tenant(monkeypatch):
    _configure(monkeypatch, app_env="local", auth_disabled=True)

    response = TestClient(_probe_app()).get("/api/v1/whoami")

    assert response.status_code == 200
    assert response.json() == {"state": LOCAL_DEV_TENANT_ID, "returned": LOCAL_DEV_TENANT_ID}


def test_local_with_explicit_disable_does_not_read_the_keys(monkeypatch, agent_runs_offline):
    """The .env.example placeholder may still be there when auth is switched off."""
    _configure(
        monkeypatch, app_env="local", keys_json=ENV_EXAMPLE_PLACEHOLDER, auth_disabled=True
    )

    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL)

    assert response.status_code == 200


def test_local_with_keys_and_no_disable_keeps_auth_on(monkeypatch, agent_runs_offline):
    _configure(
        monkeypatch, app_env="local", keys_json=_keys_json({ALPHA_KEY: "tenant-alpha"})
    )

    with TestClient(app) as client:
        missing = client.get(AGENT_RUNS_URL)
        wrong = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: WRONG_KEY})
        valid = client.get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: ALPHA_KEY})

    assert (missing.status_code, wrong.status_code, valid.status_code) == (401, 403, 200)


@pytest.mark.parametrize("app_env", ["production", "staging", "test", "dev", ""])
def test_no_keys_outside_local_is_a_startup_error_that_says_what_to_do(app_env, monkeypatch):
    _configure(monkeypatch, app_env=app_env)

    with pytest.raises(ApiKeyConfigError) as excinfo:
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line

    message = str(excinfo.value)
    assert API_KEYS_ENV in message
    assert f"APP_ENV={app_env!r}" in message
    assert "refusing" in message


@pytest.mark.parametrize("app_env", ["production", "staging", "test", "dev", ""])
@pytest.mark.parametrize("with_keys", [False, True])
def test_disable_outside_local_is_a_startup_error(app_env, with_keys, monkeypatch):
    """CORTEX_AUTH_DISABLED=true never opens anything outside APP_ENV=local."""
    keys_json = _keys_json({ALPHA_KEY: "tenant-alpha"}) if with_keys else ""
    _configure(monkeypatch, app_env=app_env, keys_json=keys_json, auth_disabled=True)

    with pytest.raises(ApiKeyConfigError) as excinfo:
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line

    message = str(excinfo.value)
    assert AUTH_DISABLED_ENV in message
    assert "refusing to start" in message
    assert ALPHA_KEY not in message
    assert hash_api_key(ALPHA_KEY) not in message


def test_disable_outside_local_never_serves_api_v1(monkeypatch, agent_runs_offline):
    """Even if startup validation were bypassed, a request is not let through."""
    _configure(
        monkeypatch,
        app_env="production",
        keys_json=_keys_json({ALPHA_KEY: "tenant-alpha"}),
        auth_disabled=True,
    )

    with pytest.raises(ApiKeyConfigError):
        TestClient(app).get(AGENT_RUNS_URL)
    with pytest.raises(ApiKeyConfigError):
        TestClient(app).get(AGENT_RUNS_URL, headers={API_KEY_HEADER_NAME: ALPHA_KEY})


def _code_defaults(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with nothing configured: no .env file, no environment variable."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(_env_file=None)


def test_app_env_is_not_local_unless_it_is_set(monkeypatch):
    """Both must be explicit: the default APP_ENV grants no local convenience."""
    defaults = _code_defaults(monkeypatch)

    assert defaults.app_env == "production"
    assert defaults.is_local is False
    assert defaults.cortex_auth_disabled is False
    assert defaults.cortex_api_keys_json == ""


def test_nothing_configured_refuses_to_start(monkeypatch):
    defaults = _code_defaults(monkeypatch)
    _configure(
        monkeypatch,
        app_env=defaults.app_env,
        keys_json=defaults.cortex_api_keys_json,
        auth_disabled=defaults.cortex_auth_disabled,
    )

    with pytest.raises(ApiKeyConfigError):
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line


def test_disable_alone_without_an_explicit_app_env_refuses_to_start(monkeypatch):
    """CORTEX_AUTH_DISABLED=true and nothing else: APP_ENV=local was not asked for."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv(AUTH_DISABLED_ENV, "true")
    only_disable = Settings(_env_file=None)
    _configure(
        monkeypatch,
        app_env=only_disable.app_env,
        keys_json=only_disable.cortex_api_keys_json,
        auth_disabled=only_disable.cortex_auth_disabled,
    )

    assert only_disable.cortex_auth_disabled is True
    with pytest.raises(ApiKeyConfigError):
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line


@pytest.mark.parametrize("app_env", ["local", "LOCAL", " Local "])
def test_local_is_matched_without_case_or_spaces(app_env, monkeypatch):
    _configure(monkeypatch, app_env=app_env, auth_disabled=True)

    assert auth.auth_config().enabled is False


def test_auth_is_enabled_in_every_configuration_except_explicit_local_disable():
    keys_json = _keys_json({ALPHA_KEY: "tenant-alpha"})
    enabled: dict[tuple[str, bool, bool], object] = {}
    for app_env in ("local", "production", "test"):
        for has_keys in (False, True):
            for disabled in (False, True):
                try:
                    config = auth._resolve_auth(keys_json if has_keys else "", app_env, disabled)
                    enabled[(app_env, has_keys, disabled)] = config.enabled
                except ApiKeyConfigError:
                    enabled[(app_env, has_keys, disabled)] = "refuses to start"

    assert enabled == {
        ("local", False, False): True,
        ("local", False, True): False,
        ("local", True, False): True,
        ("local", True, True): False,
        ("production", False, False): "refuses to start",
        ("production", False, True): "refuses to start",
        ("production", True, False): True,
        ("production", True, True): "refuses to start",
        ("test", False, False): "refuses to start",
        ("test", False, True): "refuses to start",
        ("test", True, False): True,
        ("test", True, True): "refuses to start",
    }


def test_configured_keys_are_read_only(keys_configured):
    keys = auth.configured_api_keys()

    with pytest.raises(TypeError):
        keys["0" * 64] = "tenant-intruder"  # type: ignore[index]
