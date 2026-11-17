"""W0-FU step 5: /ready leaks nothing; the full detail goes to the log, credentials masked.

/ready is open (no API key). Its response must not carry an exception message:
those name hosts, ports, users, file paths. The detail belongs in the server
log, and even there passwords are masked.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import readiness
from app.core.readiness import redact_credentials
from app.main import app
from app.services.layer2 import data_catalog
from app.services.layer2.data_catalog import DataLoadError

READINESS_LOGGER = "cortex.app.core.readiness"

DB_PASSWORD = "Zx9-db-probe-secret"
REDIS_PASSWORD = "Rq7-redis-probe-secret"
ENCODED_PASSWORD = "p%40ss%2Fw0rd-probe"  # "p@ss/w0rd-probe" percent-encoded
DECODED_PASSWORD = "p@ss/w0rd-probe"


async def _ok() -> None:
    return None


@pytest.fixture
def dependencies_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "check_database", _ok)
    monkeypatch.setattr(readiness, "check_redis", _ok)


@pytest.fixture
def secret_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "database_url",
        f"postgresql+asyncpg://dbuser:{DB_PASSWORD}@db.internal:5432/cortexdb",
    )
    monkeypatch.setattr(settings, "redis_url", f"redis://:{REDIS_PASSWORD}@cache.internal:6379/0")


def _failing(message: str, error: type[Exception] = ConnectionRefusedError):
    async def _down() -> None:
        try:
            raise OSError("low-level socket failure on 10.1.2.3")
        except OSError as cause:
            raise error(message) from cause

    return _down


# --------------------------------------------------------------------------- #
# the response
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("failing", ["check_database", "check_redis"])
def test_response_carries_no_exception_detail(
    failing, dependencies_up, secret_urls, monkeypatch, caplog
):
    message = (
        f"cannot connect to postgresql+asyncpg://dbuser:{DB_PASSWORD}@db.internal:5432/cortexdb "
        f'(user "dbuser", database "cortexdb"), also tried redis://:{REDIS_PASSWORD}@cache.internal'
    )
    monkeypatch.setattr(readiness, failing, _failing(message))

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        with TestClient(app) as client:
            response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"status", "checks"}
    assert body["status"] == "not_ready"
    assert set(body["checks"]) == {"database", "redis", "data"}
    for leaked in (
        message,
        DB_PASSWORD,
        REDIS_PASSWORD,
        "db.internal",
        "cache.internal",
        "dbuser",
        "cortexdb",
        "10.1.2.3",
        "socket",
        "Traceback",
        "://",
    ):
        assert leaked not in response.text, leaked


def test_response_carries_no_data_load_detail(
    dependencies_up, secret_urls, data_copy, fresh_data_store, caplog
):
    """A failed data load names absolute server paths: log only."""
    missing = data_copy / "sea" / "sea_c_port_capability.json"
    missing.unlink()
    with pytest.raises(DataLoadError):
        data_catalog.init_data(data_copy)

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        response = TestClient(app).get("/ready")  # no lifespan: ask the app as it is

    assert response.status_code == 503
    assert response.json()["checks"]["data"] != "ok"
    assert str(data_copy) not in response.text
    assert "sea_c_port_capability" not in response.text
    assert "/" not in response.text
    # ...and the log has it.
    assert str(missing.resolve()) in caplog.text


def test_ready_response_when_everything_is_up_is_unchanged(dependencies_up):
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok", "data": "ok"},
    }


# --------------------------------------------------------------------------- #
# the log: full detail, credentials masked
# --------------------------------------------------------------------------- #
def test_log_has_the_full_exception_detail(dependencies_up, secret_urls, monkeypatch, caplog):
    monkeypatch.setattr(
        readiness, "check_database", _failing("connection to db.internal:5432 refused")
    )

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        with TestClient(app) as client:
            client.get("/ready")

    records = [r for r in caplog.records if r.name == READINESS_LOGGER]
    assert len(records) == 1
    logged = records[0].getMessage()
    assert "ready.check_failed check=database error=ConnectionRefusedError" in logged
    # type + message, the cause chain, and the traceback
    assert "ConnectionRefusedError: connection to db.internal:5432 refused" in logged
    assert "OSError: low-level socket failure on 10.1.2.3" in logged
    assert "The above exception was the direct cause" in logged
    assert "Traceback (most recent call last)" in logged
    assert "in _down" in logged


def test_log_masks_the_credentials_of_a_url_in_the_message(
    dependencies_up, secret_urls, monkeypatch, caplog
):
    message = (
        f"cannot connect to postgresql+asyncpg://dbuser:{DB_PASSWORD}@db.internal:5432/cortexdb"
    )
    monkeypatch.setattr(readiness, "check_database", _failing(message))

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        with TestClient(app) as client:
            client.get("/ready")

    assert DB_PASSWORD not in caplog.text
    assert "dbuser:" not in caplog.text
    # Still useful: where it tried to connect is kept.
    assert "postgresql+asyncpg://***@db.internal:5432/cortexdb" in caplog.text


@pytest.mark.parametrize("failing", ["check_database", "check_redis"])
def test_log_masks_a_configured_password_wherever_it_appears(
    failing, dependencies_up, secret_urls, monkeypatch, caplog
):
    """Not only inside a URL: a driver could put the bare password in its text."""
    message = f"auth failed, tried {DB_PASSWORD!r} then {REDIS_PASSWORD!r}"
    monkeypatch.setattr(readiness, failing, _failing(message, RuntimeError))

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        with TestClient(app) as client:
            client.get("/ready")

    assert "ready.check_failed" in caplog.text
    assert DB_PASSWORD not in caplog.text
    assert REDIS_PASSWORD not in caplog.text
    assert "auth failed, tried '***' then '***'" in caplog.text


def test_log_masks_a_percent_encoded_password_in_both_forms(
    dependencies_up, monkeypatch, caplog
):
    monkeypatch.setattr(
        settings, "database_url", f"postgresql+asyncpg://dbuser:{ENCODED_PASSWORD}@db.internal/x"
    )
    message = f"encoded {ENCODED_PASSWORD} decoded {DECODED_PASSWORD}"
    monkeypatch.setattr(readiness, "check_database", _failing(message, RuntimeError))

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        with TestClient(app) as client:
            client.get("/ready")

    assert ENCODED_PASSWORD not in caplog.text
    assert DECODED_PASSWORD not in caplog.text
    assert "encoded *** decoded ***" in caplog.text


def test_timeout_is_logged_without_crashing(dependencies_up, monkeypatch, caplog):
    import asyncio

    async def _hang() -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(readiness, "check_redis", _hang)
    monkeypatch.setattr(readiness, "CHECK_TIMEOUT_SECONDS", 0.05)

    with caplog.at_level(logging.WARNING, logger=READINESS_LOGGER):
        with TestClient(app) as client:
            response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "timeout"
    assert "ready.check_failed check=redis error=TimeoutError" in caplog.text


# --------------------------------------------------------------------------- #
# redact_credentials
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "postgresql+asyncpg://user:pw-one@db:5432/name",
            "postgresql+asyncpg://***@db:5432/name",
        ),
        ("redis://:pw-two@cache:6379/0", "redis://***@cache:6379/0"),
        ("rediss://only-user@cache:6379/0", "rediss://***@cache:6379/0"),
        (
            "see https://sqlalche.me/e/20/e3q8 for help",
            "see https://sqlalche.me/e/20/e3q8 for help",
        ),
        (
            "host=db port=5432 user=u password=pw-three dbname=n",
            "host=db port=5432 user=u password=*** dbname=n",
        ),
        ("Password: 'pw four' was refused", "Password: *** was refused"),
        (
            '{"user": "u", "password": "pw five", "port": 1}',
            '{"user": "u", "password": ***, "port": 1}',
        ),
        (
            'password authentication failed for user "cortexuser"',
            'password authentication failed for user "cortexuser"',
        ),
        (
            "Error 61 connecting to 127.0.0.1:6379. Connection refused.",
            "Error 61 connecting to 127.0.0.1:6379. Connection refused.",
        ),
    ],
)
def test_redact_credentials(text, expected, monkeypatch):
    # Passwords unrelated to the texts above, so only the patterns are at work.
    monkeypatch.setattr(settings, "database_url", f"postgresql+asyncpg://u:{DB_PASSWORD}@h/d")
    monkeypatch.setattr(settings, "redis_url", f"redis://:{REDIS_PASSWORD}@h/0")

    assert redact_credentials(text) == expected


def test_redact_credentials_survives_an_unparsable_configured_url(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:pw@[not-an-ip/d")
    monkeypatch.setattr(settings, "redis_url", "")

    assert redact_credentials("nothing secret here") == "nothing secret here"
