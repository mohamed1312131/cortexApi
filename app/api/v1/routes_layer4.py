from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.schemas.layer4 import Layer4ReportRequest, Layer4Result
from app.services.layer4 import build_layer4_report


router = APIRouter(prefix="/api/v1/layer4", tags=["layer4"])
logger = get_logger(__name__)


@router.post("/report", response_model=Layer4Result)
async def layer4_report(payload: Layer4ReportRequest) -> Layer4Result:
    trace_id = str(uuid4())
    logger.info(
        "layer4.received trace_id=%s endpoint=/api/v1/layer4/report case_id=%s",
        trace_id,
        payload.case_id,
    )

    def call() -> Layer4Result:
        return build_layer4_report(payload, trace_id=trace_id, run_order=5)

    try:
        result = await asyncio.to_thread(call)
        logger.info(
            "layer4.outcome trace_id=%s case_id=%s",
            trace_id,
            result.case_id,
        )
        return result
    except RuntimeError as exc:
        logger.warning("layer4.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("layer4.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("layer4.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _err(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"
