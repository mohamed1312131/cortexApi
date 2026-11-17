"""W0-FU step 6: /docs, /redoc and /openapi.json exist only with APP_ENV=local."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main
from app.config import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]
NOT_LOCAL = ["production", "staging", "test", "dev", ""]


def _app_for(monkeypatch: pytest.MonkeyPatch, app_env: str) -> FastAPI:
    monkeypatch.setattr(settings, "app_env", app_env)
    return main.create_app()


def _paths(application: FastAPI) -> set[str]:
    return {route.path for route in application.routes}


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_are_available_when_local(path, monkeypatch):
    response = TestClient(_app_for(monkeypatch, "local")).get(path)

    assert response.status_code == 200


def test_local_openapi_schema_describes_the_api(monkeypatch):
    schema = TestClient(_app_for(monkeypatch, "local")).get("/openapi.json").json()

    assert schema["info"]["title"] == "cortex-api"
    assert "/api/v1/cortex/full-message" in schema["paths"]


@pytest.mark.parametrize("app_env", NOT_LOCAL)
@pytest.mark.parametrize("path", [*DOC_PATHS, "/docs/oauth2-redirect"])
def test_docs_are_hidden_outside_local(app_env, path, monkeypatch):
    application = _app_for(monkeypatch, app_env)

    response = TestClient(application).get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert path not in _paths(application)


def test_docs_are_hidden_when_app_env_is_not_set(monkeypatch):
    """The default APP_ENV is not local, so nothing configured = no docs."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    application = _app_for(monkeypatch, Settings(_env_file=None).app_env)

    client = TestClient(application)

    assert [client.get(path).status_code for path in DOC_PATHS] == [404, 404, 404]
    assert (application.openapi_url, application.docs_url, application.redoc_url) == (
        None,
        None,
        None,
    )


def test_hiding_the_docs_removes_nothing_else(monkeypatch):
    local_app = _app_for(monkeypatch, "local")
    production_app = _app_for(monkeypatch, "production")

    def api_routes(application: FastAPI) -> set[tuple[str, str]]:
        return {
            (method, route.path)
            for route in application.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }

    assert api_routes(production_app) == api_routes(local_app)
    assert _paths(local_app) - _paths(production_app) == {
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    }
    assert TestClient(production_app).get("/health").status_code == 200


@pytest.mark.real_auth
def test_docs_being_visible_does_not_open_the_api(monkeypatch):
    """local shows the docs; /api/v1 still needs a key (no key is configured here)."""
    monkeypatch.setattr(settings, "cortex_api_keys_json", "")
    application = _app_for(monkeypatch, "local")

    client = TestClient(application)

    assert client.get("/docs").status_code == 200
    assert client.get("/api/v1/cortex/cases/case-x/agent-runs").status_code == 401


@pytest.mark.parametrize(
    ("app_env", "expected"),
    [
        ("local", "/docs /redoc /openapi.json"),
        ("production", "None None None"),
    ],
)
def test_the_served_app_object_follows_app_env(app_env, expected):
    """``app.main.app`` (what uvicorn serves) in a fresh interpreter."""
    env = dict(os.environ)  # carries the pinned test key
    env["APP_ENV"] = app_env
    env["PYTHONPATH"] = str(REPO_ROOT)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.main import app; print(app.docs_url, app.redoc_url, app.openapi_url)",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip().splitlines()[-1] == expected
