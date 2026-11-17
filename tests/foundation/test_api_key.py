"""Temporary API-key auth (Wave 0): X-API-Key on every /api/v1 route.

Missing key -> 401, wrong key -> 403, valid key -> 200. /health and /ready stay open.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.v1 import routes_agent_runs
from app.config import settings
from app.core import auth, readiness
from app.core.auth import (
    API_KEY_HEADER_NAME,
    ApiKeyConfigError,
    hash_api_key,
    parse_api_keys,
    require_api_key,
)
from app.main import API_V1_PREFIX, app
from app.repositories.agent_run_repository import InMemoryAgentRunRepository

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


def test_no_keys_in_local_env_switches_auth_off(monkeypatch, agent_runs_offline):
    """Local development and the offline suite run without keys (documented)."""
    monkeypatch.setattr(settings, "cortex_api_keys_json", "")
    monkeypatch.setattr(settings, "app_env", "local")

    with TestClient(app) as client:
        response = client.get(AGENT_RUNS_URL)

    assert response.status_code == 200
