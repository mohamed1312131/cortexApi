from __future__ import annotations

import asyncio
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.core.logging import get_logger
from app.schemas import CortexFullOrchestratorResult, CortexOrchestratorResult, IntakeMessageRequest
from app.services.layer1.conversation_lock import conversation_guard
from app.services.layer1.response_sanitizer import sanitize_intake_result
from app.services.orchestrator import handle_cortex_message, handle_full_cortex_message
from app.services.orchestrator.artifact_cache import CacheRead, OrchestratorArtifactCache


router = APIRouter(prefix="/api/v1/cortex", tags=["cortex"])
logger = get_logger(__name__)
_ARTIFACT_CACHE: OrchestratorArtifactCache | None = None


@router.post("/message", response_model=CortexOrchestratorResult)
async def cortex_message(payload: IntakeMessageRequest) -> CortexOrchestratorResult:
    trace_id = str(uuid4())
    logger.info(
        "cortex.received trace_id=%s endpoint=/api/v1/cortex/message conversation_id=%s case_id=%s",
        trace_id,
        payload.conversation_id,
        payload.case_id,
    )
    def call() -> CortexOrchestratorResult:
        # Serialize concurrent messages for the same conversation_id; different
        # conversations stay concurrent. Guard wraps the whole Layer 1 request
        # (and the gated Layer 2 build) for this conversation.
        with conversation_guard(payload.conversation_id):
            return handle_cortex_message(
                conversation_id=payload.conversation_id,
                case_id=payload.case_id,
                user_id=payload.user_id,
                company_id=payload.company_id,
                message=payload.message,
                trace_id=trace_id,
            )

    try:
        result = await asyncio.to_thread(call)
        # Boundary parity with /api/v1/intake/message: re-assert user-facing
        # sanitization on the embedded layer1 object so the product endpoint
        # never returns dirty strings even if an upstream path skipped it. This
        # only touches user-facing text; the orchestrator gate is untouched.
        result.layer1 = sanitize_intake_result(result.layer1)
        return result
    except RuntimeError as exc:
        logger.warning("cortex.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("cortex.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("cortex.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _err(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"


@router.post("/full-message", response_model=CortexFullOrchestratorResult)
async def cortex_full_message(payload: IntakeMessageRequest) -> CortexFullOrchestratorResult:
    trace_id = str(uuid4())
    logger.info(
        "cortex.received trace_id=%s endpoint=/api/v1/cortex/full-message conversation_id=%s case_id=%s",
        trace_id,
        payload.conversation_id,
        payload.case_id,
    )

    def call() -> CortexFullOrchestratorResult:
        with conversation_guard(payload.conversation_id):
            return handle_full_cortex_message(
                conversation_id=payload.conversation_id,
                case_id=payload.case_id,
                user_id=payload.user_id,
                company_id=payload.company_id,
                message=payload.message,
                trace_id=trace_id,
            )

    try:
        return await asyncio.to_thread(call)
    except RuntimeError as exc:
        logger.warning("cortex.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("cortex.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("cortex.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/artifacts/{artifact}")
async def cortex_artifact(
    artifact: Literal["layer2", "layer3", "layer4"],
    case_id: str | None = None,
    shipment_request_version: int | None = None,
    key: str | None = Query(default=None, description="Exact Redis artifact key from artifact_refs."),
) -> dict:
    """Fetch full debug artifacts from the orchestrator artifact cache.

    Normal /full-message responses are compact by default. This endpoint is the
    explicit escape hatch for dev/debug tooling that needs the stored full
    artifacts.
    """
    read = _read_artifact(
        artifact=artifact,
        case_id=case_id,
        shipment_request_version=shipment_request_version,
        key=key,
    )
    if read.value is None:
        status_code = 503 if read.status in {"disabled", "unavailable", "error"} else 404
        raise HTTPException(
            status_code=status_code,
            detail={
                "artifact": artifact,
                "status": read.status,
                "key": read.key,
                "error": read.error,
            },
        )
    return {
        "artifact": artifact,
        "status": read.status,
        "key": read.key,
        "payload": read.value.model_dump(mode="json"),
    }


def _read_artifact(
    *,
    artifact: Literal["layer2", "layer3", "layer4"],
    case_id: str | None,
    shipment_request_version: int | None,
    key: str | None,
) -> CacheRead:
    if key is not None:
        _validate_artifact_key(key, artifact)
        if artifact == "layer2":
            return _artifact_cache().get_layer2_by_key(key)
        if artifact == "layer3":
            return _artifact_cache().get_layer3_by_key(key)
        return _artifact_cache().get_layer4_by_key(key)

    if artifact == "layer4":
        raise HTTPException(
            status_code=400,
            detail="Layer 4 artifact lookup requires key from artifact_refs.",
        )
    if case_id is None or shipment_request_version is None:
        raise HTTPException(
            status_code=400,
            detail="case_id and shipment_request_version are required when key is not provided.",
        )
    if artifact == "layer2":
        return _artifact_cache().get_layer2(
            case_id=case_id,
            shipment_request_version=shipment_request_version,
        )
    return _artifact_cache().get_layer3(
        case_id=case_id,
        shipment_request_version=shipment_request_version,
    )


def _validate_artifact_key(key: str, artifact: str) -> None:
    if not key.startswith("cortex:orchestrator:case:") or f":{artifact}" not in key:
        raise HTTPException(status_code=400, detail="Invalid artifact key for requested artifact.")


def _artifact_cache() -> OrchestratorArtifactCache:
    global _ARTIFACT_CACHE
    if _ARTIFACT_CACHE is None:
        _ARTIFACT_CACHE = OrchestratorArtifactCache()
    return _ARTIFACT_CACHE
