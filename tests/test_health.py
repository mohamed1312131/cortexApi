import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core import readiness
from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


# --------------------------------------------------------------------------- #
# /ready: database reachable, Redis reachable, data loaded
# --------------------------------------------------------------------------- #
async def _ok() -> None:
    return None


@pytest.fixture
def dependencies_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "check_database", _ok)
    monkeypatch.setattr(readiness, "check_redis", _ok)


def test_ready_when_database_redis_and_data_are_ok(dependencies_up) -> None:
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok", "data": "ok"},
    }


@pytest.mark.parametrize("failing", ["check_database", "check_redis"])
def test_not_ready_when_a_dependency_is_unreachable(failing, dependencies_up, monkeypatch) -> None:
    secret_detail = "postgresql://user:password@db.internal:5432/cortex refused"

    async def _down() -> None:
        raise ConnectionRefusedError(secret_detail)

    monkeypatch.setattr(readiness, failing, _down)

    with TestClient(app) as client:
        response = client.get("/ready")

    name = failing.removeprefix("check_")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"][name] == "error: ConnectionRefusedError"
    assert {key for key, value in body["checks"].items() if value == "ok"} == (
        {"database", "redis", "data"} - {name}
    )
    # Connection details stay in the server log, never in the response.
    assert secret_detail not in response.text
    assert "password" not in response.text


def test_not_ready_when_a_check_hangs(dependencies_up, monkeypatch) -> None:
    async def _hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(readiness, "check_redis", _hang)
    monkeypatch.setattr(readiness, "CHECK_TIMEOUT_SECONDS", 0.05)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "timeout"


def test_health_stays_alive_when_not_ready(dependencies_up, monkeypatch) -> None:
    """Liveness and readiness are separate: a down database does not fail /health."""

    async def _down() -> None:
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(readiness, "check_database", _down)

    with TestClient(app) as client:
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200
