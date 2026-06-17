"""Production-concurrency hardening tests.

Covers the per-conversation guard, Redis production mode (fallback enabled vs
disabled), and the configurable worker/thread/fallback settings. All offline
and deterministic.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest
from redis import RedisError

from app.config import Settings, settings
from app.schemas import CaseState
from app.services.layer1.case_state_manager import (
    RedisCaseStateStore,
    RedisFallbackDisabledError,
)
from app.services.layer1.conversation_lock import conversation_guard


class _BoomRedis:
    @staticmethod
    def get(*args, **kwargs):
        raise RedisError("connection refused")

    @staticmethod
    def setex(*args, **kwargs):
        raise RedisError("connection refused")


# --------------------------------------------------------------------------- #
# Per-conversation guard
# --------------------------------------------------------------------------- #
def test_same_conversation_is_serialized(monkeypatch):
    monkeypatch.setattr(settings, "cortex_redis_fallback_enabled", True)  # local lock
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def work():
        nonlocal active, max_active
        with conversation_guard("conv-same"):
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_active == 1  # never two at once for the same conversation


def test_different_conversations_run_concurrently(monkeypatch):
    monkeypatch.setattr(settings, "cortex_redis_fallback_enabled", True)  # local lock
    n = 5
    barrier = threading.Barrier(n, timeout=3)
    errors: list[Exception] = []

    def work(conversation_id: str):
        try:
            with conversation_guard(conversation_id):
                # If different conversations were serialized, not all n threads
                # could be inside the guard at once and the barrier would time out.
                barrier.wait()
        except threading.BrokenBarrierError as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(f"conv-{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors  # all reached the barrier simultaneously -> concurrent


def test_guard_is_noop_without_conversation_id():
    with conversation_guard(None):
        pass  # must not raise / block


# --------------------------------------------------------------------------- #
# Redis production mode
# --------------------------------------------------------------------------- #
def test_redis_fallback_disabled_fails_loudly():
    store = RedisCaseStateStore("redis://localhost:6390/0", fallback_enabled=False)
    store._redis = _BoomRedis()

    with pytest.raises(RedisFallbackDisabledError):
        store.get("CASE-PROD")
    with pytest.raises(RedisFallbackDisabledError):
        store.save(CaseState(case_id="CASE-PROD", conversation_id="conv-prod"))


def test_redis_fallback_enabled_logs_and_continues(caplog):
    store = RedisCaseStateStore("redis://localhost:6390/0", fallback_enabled=True)
    store._redis = _BoomRedis()

    with caplog.at_level(logging.WARNING, logger="cortex"):
        assert store.get("CASE-DEV") is None
        store.save(CaseState(case_id="CASE-DEV", conversation_id="conv-dev"))

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("case_state.redis_fallback" in m for m in messages)
    assert any("op=save" in m for m in messages)


# --------------------------------------------------------------------------- #
# Settings read from env
# --------------------------------------------------------------------------- #
def test_runtime_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("CORTEX_API_WORKERS", "7")
    monkeypatch.setenv("CORTEX_API_THREAD_WORKERS", "55")
    monkeypatch.setenv("CORTEX_REDIS_FALLBACK_ENABLED", "false")

    fresh = Settings(_env_file=None)
    assert fresh.cortex_api_workers == 7
    assert fresh.cortex_api_thread_workers == 55
    assert fresh.cortex_redis_fallback_enabled is False


def test_runtime_settings_defaults():
    fresh = Settings(_env_file=None)
    assert fresh.cortex_api_workers == 1
    assert fresh.cortex_api_thread_workers == 0
    assert fresh.cortex_redis_fallback_enabled is True
