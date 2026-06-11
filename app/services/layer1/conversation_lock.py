"""Per-conversation serialization guard for Layer 1 requests.

Two messages for the *same* ``conversation_id`` must not interleave through the
load_case_context -> ... -> persist_intake_state pipeline, or one can clobber the
other's state. Different ``conversation_id`` values stay fully concurrent.

This guard is applied at the route/service boundary (it wraps the whole Layer 1
request), so the Layer 1 graph shape is untouched. Because that work runs inside
``asyncio.to_thread`` (a worker thread), the guard is a synchronous context
manager using thread-level locks.

Backends:
  * In-process ``threading.Lock`` keyed by ``conversation_id`` — correct for a
    single worker (the default and all local dev).
  * Redis lock — used when ``cortex_redis_fallback_enabled`` is False (the
    production stance), so the guard holds across multiple worker processes. If
    the Redis lock cannot be taken, we log and degrade to the local lock.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from redis import RedisError

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_REGISTRY_LOCK = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.Lock] = {}

# How long a single Layer 1 request may hold the conversation lock before the
# Redis lease auto-expires (safety against a crashed holder).
_LOCK_TIMEOUT_SECONDS = 30


def _local_lock_for(conversation_id: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        lock = _LOCAL_LOCKS.get(conversation_id)
        if lock is None:
            lock = threading.Lock()
            _LOCAL_LOCKS[conversation_id] = lock
        return lock


def _redis_client_for_locking():
    """Return the shared Redis client when production locking is in effect."""
    if settings.cortex_redis_fallback_enabled:
        return None
    # Imported lazily to avoid a circular import at module load.
    from app.services.layer1.case_state_manager import case_state_store

    return getattr(case_state_store, "_redis", None)


@contextmanager
def _local_guard(conversation_id: str) -> Iterator[None]:
    lock = _local_lock_for(conversation_id)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def conversation_guard(conversation_id: str | None) -> Iterator[None]:
    """Serialize concurrent work for one ``conversation_id``.

    No-op when ``conversation_id`` is falsy (ad-hoc requests with no conversation
    cannot collide on shared conversation state).
    """
    if not conversation_id:
        yield
        return

    redis_client = _redis_client_for_locking()
    if redis_client is not None:
        lock = redis_client.lock(
            f"cortex:lock:conversation:{conversation_id}",
            timeout=_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=_LOCK_TIMEOUT_SECONDS,
        )
        try:
            acquired = lock.acquire()
        except RedisError as exc:
            logger.warning(
                "conversation_lock.redis_error conversation_id=%s error=%s "
                "(falling back to in-process lock)",
                conversation_id,
                f"{exc.__class__.__name__}: {exc}",
            )
            acquired = False

        if acquired:
            try:
                yield
            finally:
                try:
                    lock.release()
                except RedisError:
                    pass
            return
        # Could not take the Redis lock; degrade to the in-process lock.

    with _local_guard(conversation_id):
        yield
