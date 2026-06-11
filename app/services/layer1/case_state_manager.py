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

from pydantic import BaseModel
from redis import Redis, RedisError

from app.config import settings
from app.core.logging import get_logger
from app.schemas import CaseState, ValidatedShipmentRequest


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


def merge_requests(
    existing: ValidatedShipmentRequest | None,
    incoming: ValidatedShipmentRequest,
) -> tuple[ValidatedShipmentRequest, list[str]]:
    if existing is None:
        return incoming, _present_fields(incoming)

    merged = existing.model_copy(deep=True)
    changed_fields: list[str] = []

    _merge_model_fields(
        merged.core_shipment,
        incoming.core_shipment,
        "core_shipment",
        changed_fields,
    )
    _merge_model_fields(merged.lane, incoming.lane, "lane", changed_fields)
    _merge_model_fields(merged.commercial, incoming.commercial, "commercial", changed_fields)

    if incoming.mode.requested_mode.value != "unknown":
        if merged.mode.requested_mode != incoming.mode.requested_mode:
            merged.mode.requested_mode = incoming.mode.requested_mode
            merged.mode.candidate_modes = incoming.mode.candidate_modes
            merged.mode.needs_mode_selection = incoming.mode.needs_mode_selection
            changed_fields.append("mode.requested_mode")

    _merge_model_fields(merged.cargo_flags, incoming.cargo_flags, "cargo_flags", changed_fields)

    for profile in incoming.active_profiles:
        if profile not in merged.active_profiles:
            merged.active_profiles.append(profile)
            changed_fields.append("active_profiles")

    for profile_name, profile_payload in incoming.profiles.items():
        current = merged.profiles.setdefault(profile_name, {})
        if isinstance(current, dict) and isinstance(profile_payload, dict):
            for key, value in profile_payload.items():
                if value is not None and current.get(key) != value:
                    current[key] = value
                    changed_fields.append(f"profiles.{profile_name}.{key}")
        elif profile_payload is not None and current != profile_payload:
            merged.profiles[profile_name] = profile_payload
            changed_fields.append(f"profiles.{profile_name}")

    merged.facts_from_user.update(incoming.facts_from_user)
    merged.inferred_flags.update(incoming.inferred_flags)
    merged.field_confidence.update(incoming.field_confidence)

    return merged, _dedupe(changed_fields)


def _merge_model_fields(
    target: BaseModel,
    incoming: BaseModel,
    prefix: str,
    changed_fields: list[str],
) -> None:
    for field_name in incoming.__class__.model_fields:
        value = getattr(incoming, field_name)
        if value is None:
            continue
        if hasattr(value, "value") and value.value == "unknown":
            continue
        if getattr(target, field_name) != value:
            setattr(target, field_name, value)
            changed_fields.append(f"{prefix}.{field_name}")


def _present_fields(request: ValidatedShipmentRequest) -> list[str]:
    fields: list[str] = []
    for group_name in ("core_shipment", "lane", "commercial"):
        group = getattr(request, group_name)
        for field_name in group.__class__.model_fields:
            if getattr(group, field_name) is not None:
                fields.append(f"{group_name}.{field_name}")
    for field_name in request.cargo_flags.__class__.model_fields:
        value = getattr(request.cargo_flags, field_name)
        if getattr(value, "value", None) not in {None, "unknown"}:
            fields.append(f"cargo_flags.{field_name}")
    return _dedupe(fields)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _case_key(case_id: str) -> str:
    return f"cortex:layer1:case:{case_id}"


def _conversation_key(conversation_id: str) -> str:
    return f"cortex:layer1:conversation:{conversation_id}:active_case"


case_state_store = RedisCaseStateStore(
    settings.redis_url,
    fallback_enabled=settings.cortex_redis_fallback_enabled,
)
