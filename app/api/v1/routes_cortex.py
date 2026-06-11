from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.schemas import CortexOrchestratorResult, IntakeMessageRequest
from app.services.layer1.conversation_lock import conversation_guard
from app.services.layer1.response_sanitizer import sanitize_intake_result
from app.services.orchestrator import handle_cortex_message


router = APIRouter(prefix="/api/v1/cortex", tags=["cortex"])
logger = get_logger(__name__)


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
