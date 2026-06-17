from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from redis import Redis, RedisError

from app.config import settings
from app.core.logging import get_logger
from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import Layer3Result
from app.schemas.layer4 import Layer4ReportRequest, Layer4Result

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class CacheRead(Generic[T]):
    value: T | None
    status: str
    key: str | None = None
    error: str | None = None


class OrchestratorArtifactCache:
    """Redis-backed cache for expensive full-orchestrator artifacts.

    Cache failures are intentionally non-fatal. The orchestrator should still run
    normally when Redis is unavailable, a cached payload is stale, or validation
    fails after a schema change.
    """

    def __init__(self) -> None:
        self._enabled = bool(settings.orchestrator_cache_enabled)
        self._ttl_seconds = settings.orchestrator_cache_ttl_seconds
        self._redis: Redis | None = (
            Redis.from_url(settings.redis_url, decode_responses=True)
            if self._enabled
            else None
        )

    def get_layer2(self, *, case_id: str, shipment_request_version: int | None) -> CacheRead[FactPackage]:
        return self._get(
            key=self._artifact_key(case_id, shipment_request_version, "layer2"),
            model=FactPackage,
            artifact="layer2",
        )

    def set_layer2(
        self,
        value: FactPackage,
        *,
        case_id: str,
        shipment_request_version: int | None,
    ) -> str:
        return self._set(
            key=self._artifact_key(case_id, shipment_request_version, "layer2"),
            value=value,
            artifact="layer2",
        )

    def get_layer3(self, *, case_id: str, shipment_request_version: int | None) -> CacheRead[Layer3Result]:
        return self._get(
            key=self._artifact_key(case_id, shipment_request_version, "layer3"),
            model=Layer3Result,
            artifact="layer3",
        )

    def set_layer3(
        self,
        value: Layer3Result,
        *,
        case_id: str,
        shipment_request_version: int | None,
    ) -> str:
        return self._set(
            key=self._artifact_key(case_id, shipment_request_version, "layer3"),
            value=value,
            artifact="layer3",
        )

    def get_layer4(
        self,
        request: Layer4ReportRequest,
        *,
        shipment_request_version: int | None,
    ) -> CacheRead[Layer4Result]:
        return self._get(
            key=self._layer4_key(request, shipment_request_version),
            model=Layer4Result,
            artifact="layer4",
        )

    def set_layer4(
        self,
        value: Layer4Result,
        request: Layer4ReportRequest,
        *,
        shipment_request_version: int | None,
    ) -> str:
        return self._set(
            key=self._layer4_key(request, shipment_request_version),
            value=value,
            artifact="layer4",
        )

    def _get(self, *, key: str | None, model: type[T], artifact: str) -> CacheRead[T]:
        if not self._enabled:
            return CacheRead(value=None, status="disabled", key=key)
        if key is None:
            return CacheRead(value=None, status="skipped_no_version", key=key)
        if self._redis is None:
            return CacheRead(value=None, status="unavailable", key=key)
        try:
            payload = self._redis.get(key)
            if not payload:
                return CacheRead(value=None, status="miss", key=key)
            return CacheRead(value=model.model_validate_json(payload), status="hit", key=key)
        except (RedisError, ValidationError, ValueError, TypeError) as exc:
            detail = f"{exc.__class__.__name__}: {exc}"
            logger.warning(
                "orchestrator_cache.get_failed artifact=%s key=%s error=%s",
                artifact,
                key,
                detail,
            )
            self._delete_quietly(key, artifact=artifact)
            return CacheRead(value=None, status="error", key=key, error=detail)

    def _set(self, *, key: str | None, value: BaseModel, artifact: str) -> str:
        if not self._enabled:
            return "disabled"
        if key is None:
            return "skipped_no_version"
        if self._redis is None:
            return "unavailable"
        try:
            self._redis.setex(key, self._ttl_seconds, value.model_dump_json())
            return "stored"
        except (RedisError, ValueError, TypeError) as exc:
            logger.warning(
                "orchestrator_cache.set_failed artifact=%s key=%s error=%s: %s",
                artifact,
                key,
                exc.__class__.__name__,
                exc,
            )
            return "error"

    def _delete_quietly(self, key: str, *, artifact: str) -> None:
        if self._redis is None:
            return
        try:
            self._redis.delete(key)
        except RedisError as exc:
            logger.warning(
                "orchestrator_cache.delete_failed artifact=%s key=%s error=%s: %s",
                artifact,
                key,
                exc.__class__.__name__,
                exc,
            )

    def _artifact_key(
        self,
        case_id: str,
        shipment_request_version: int | None,
        artifact: str,
    ) -> str | None:
        if shipment_request_version is None:
            return None
        return f"cortex:orchestrator:case:{case_id}:v:{shipment_request_version}:{artifact}"

    def _layer4_key(
        self,
        request: Layer4ReportRequest,
        shipment_request_version: int | None,
    ) -> str | None:
        base = self._artifact_key(
            request.fact_package.case_id,
            shipment_request_version,
            "layer4",
        )
        if base is None:
            return None
        fingerprint = _stable_hash(
            {
                "report_type": request.report_type.value,
                "response_language": request.response_language,
                "latest_user_message": request.latest_user_message,
                "layer3_status": request.layer3_result.status.value,
                "reasoning_decision_id": (
                    request.reasoning_decision.reasoning_decision_id
                    if request.reasoning_decision is not None
                    else None
                ),
            }
        )
        return f"{base}:{fingerprint}"


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
