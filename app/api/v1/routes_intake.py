from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger, log_layer1_outcome
from app.schemas import IntakeMessageRequest, IntakeResult
from app.services.layer1 import handle_intake_message
from app.services.layer1.conversation_lock import conversation_guard
from app.services.layer1.response_sanitizer import sanitize_intake_result


router = APIRouter(prefix="/api/v1/intake", tags=["intake"])
logger = get_logger(__name__)


@router.post("/message", response_model=IntakeResult)
async def intake_message(payload: IntakeMessageRequest) -> IntakeResult:
    trace_id = str(uuid4())
    logger.info(
        "intake.received trace_id=%s endpoint=/api/v1/intake/message conversation_id=%s case_id=%s",
        trace_id,
        payload.conversation_id,
        payload.case_id,
    )
    def call() -> IntakeResult:
        # Serialize concurrent messages for the same conversation_id; different
        # conversations stay concurrent. Guard wraps the whole Layer 1 request.
        with conversation_guard(payload.conversation_id):
            return handle_intake_message(
                conversation_id=payload.conversation_id,
                case_id=payload.case_id,
                user_id=payload.user_id,
                company_id=payload.company_id,
                message=payload.message,
            )

    try:
        result = sanitize_intake_result(await asyncio.to_thread(call))
        log_layer1_outcome(
            logger, trace_id=trace_id, endpoint="/api/v1/intake/message", result=result
        )
        return result
    except RuntimeError as exc:
        logger.warning("intake.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("intake.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("intake.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _err(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"
