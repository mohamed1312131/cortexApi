"""Case-state persistence for the Layer 1 intake graph.

``RedisCaseStateStore`` keeps an in-memory ``_fallback`` so the app stays up if
Redis is unavailable. That fallback is **dev-only**: it is per-process, so under
multiple uvicorn/gunicorn workers conversation state is NOT shared and multi-turn
intake will break. Redis failures are logged (not silently swallowed) so the
degradation is visible. Production must run with a healthy shared Redis.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from redis import Redis, RedisError

from app.config import settings
from app.core.logging import get_logger
from app.schemas import CaseState


logger = get_logger(__name__)

CASE_STATE_TTL_SECONDS = 60 * 60 * 24


def new_case_id() -> str:
    return f"SHIP-{uuid4().hex[:8].upper()}"


class InMemoryCaseStateStore:
    def __init__(self) -> None:
        self._cases: dict[str, CaseState] = {}
        self._conversation_active_case: dict[str, str] = {}

    def get(self, case_id: str | None) -> CaseState | None:
        if not case_id:
            return None
        return self._cases.get(case_id)

    def get_active_for_conversation(self, conversation_id: str | None) -> CaseState | None:
        if not conversation_id:
            return None
        return self.get(self._conversation_active_case.get(conversation_id))

    def create(self, *, conversation_id: str | None = None, case_id: str | None = None) -> CaseState:
        state = CaseState(case_id=case_id or new_case_id(), conversation_id=conversation_id)
        self.save(state)
        return state

    def save(self, state: CaseState) -> CaseState:
        state.updated_at = datetime.now(timezone.utc)
        self._cases[state.case_id] = state
        if state.conversation_id:
            self._conversation_active_case[state.conversation_id] = state.case_id
        return state


class RedisFallbackDisabledError(RuntimeError):
    """Raised when Redis fails and the in-memory fallback is disabled (production)."""


class RedisCaseStateStore:
    def __init__(
        self,
        redis_url: str,
        fallback: InMemoryCaseStateStore | None = None,
        *,
        fallback_enabled: bool = True,
    ) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._fallback = fallback or InMemoryCaseStateStore()
        self._fallback_enabled = fallback_enabled

    def _on_redis_failure(self, *, op: str, key: str | None, exc: Exception) -> None:
        """Log the failure; raise if the in-memory fallback is disabled.

        When the fallback is disabled (production), we fail loudly so callers
        surface a controlled 503 instead of silently using per-process in-memory
        state that is unsafe across workers.
        """
        detail = f"{exc.__class__.__name__}: {exc}"
        if not self._fallback_enabled:
            logger.error(
                "case_state.redis_unavailable op=%s key=%s error=%s (fallback disabled)",
                op,
                key,
                detail,
            )
            raise RedisFallbackDisabledError(
                f"Redis unavailable and in-memory fallback disabled (op={op})"
            ) from exc
        logger.warning(
            "case_state.redis_fallback op=%s key=%s error=%s",
            op,
            key,
            detail,
        )

    def get(self, case_id: str | None) -> CaseState | None:
        if not case_id:
            return None
        try:
            payload = self._redis.get(_case_key(case_id))
            if payload:
                return CaseState.model_validate_json(payload)
        except (RedisError, ValueError) as exc:
            self._on_redis_failure(op="get", key=case_id, exc=exc)
            return self._fallback.get(case_id)
        return self._fallback.get(case_id)

    def get_active_for_conversation(self, conversation_id: str | None) -> CaseState | None:
        if not conversation_id:
            return None
        try:
            case_id = self._redis.get(_conversation_key(conversation_id))
            if case_id:
                return self.get(case_id)
        except RedisError as exc:
            self._on_redis_failure(
                op="get_active_for_conversation", key=conversation_id, exc=exc
            )
            return self._fallback.get_active_for_conversation(conversation_id)
        return self._fallback.get_active_for_conversation(conversation_id)

    def create(self, *, conversation_id: str | None = None, case_id: str | None = None) -> CaseState:
        state = CaseState(case_id=case_id or new_case_id(), conversation_id=conversation_id)
        self.save(state)
        return state

    def save(self, state: CaseState) -> CaseState:
        state.updated_at = datetime.now(timezone.utc)
        self._fallback.save(state)
        try:
            self._redis.setex(
                _case_key(state.case_id),
                CASE_STATE_TTL_SECONDS,
                state.model_dump_json(),
            )
            if state.conversation_id:
                self._redis.setex(
                    _conversation_key(state.conversation_id),
                    CASE_STATE_TTL_SECONDS,
                    state.case_id,
                )
        except RedisError as exc:
            self._on_redis_failure(op="save", key=state.case_id, exc=exc)
        return state


def append_message_to_summary(state: CaseState, message: str) -> CaseState:
    clean = " ".join(message.split())
    if not clean:
        return state
    if state.conversation_summary:
        state.conversation_summary = f"{state.conversation_summary}\nUser: {clean}"
    else:
        state.conversation_summary = f"User: {clean}"
    return state


def _case_key(case_id: str) -> str:
    return f"cortex:layer1:case:{case_id}"


def _conversation_key(conversation_id: str) -> str:
    return f"cortex:layer1:conversation:{conversation_id}:active_case"


case_state_store = RedisCaseStateStore(
    settings.redis_url,
    fallback_enabled=settings.cortex_redis_fallback_enabled,
)
