from __future__ import annotations

import asyncio
import copy
import json
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import Layer3Result
from app.services.layer3 import run_layer3
from app.services.layer3.safety_rules import (
    contains_forbidden_claim,
    contains_raw_score_leakage,
)

# Developer/debug endpoint for Layer 3 ONLY.
#
# It receives an already-built FactPackage and runs the standalone Layer 3 graph
# (FactPackage -> run_layer3 -> Layer3Result). It does NOT call Layer 1 or Layer 2
# and it does NOT replace /api/v1/cortex/message.

router = APIRouter(prefix="/api/v1/layer3", tags=["layer3"])
logger = get_logger(__name__)

# Internal scoring tokens that must never appear anywhere in the response.
_RAW_SCORE_TOKENS = ("raw_score", "raw_scores_by_path", "internal_scoring_trace")


def _guard_output(result: Layer3Result) -> None:
    """Final serialized-output safety guard. Never mutates/repairs — raises on leak."""
    dump = result.model_dump(mode="json")

    # 1. hard token check across the ENTIRE serialized response
    full = json.dumps(dump)
    for token in _RAW_SCORE_TOKENS:
        if token in full:
            raise ValueError(f"Layer 3 output leaked internal scoring token: {token!r}")

    # 2. forbidden-claim / raw-score scans, excluding the explicit policy list in
    #    reasoning_decision.forbidden_claims (those phrases are allowed there only).
    sanitized = copy.deepcopy(dump)
    reasoning_decision = sanitized.get("reasoning_decision")
    if isinstance(reasoning_decision, dict):
        reasoning_decision["forbidden_claims"] = []
    blob = json.dumps(sanitized)

    leaks = contains_raw_score_leakage(blob)
    if leaks:
        raise ValueError(f"Layer 3 output leaked internal scoring vocabulary: {leaks}")
    forbidden = contains_forbidden_claim(blob)
    if forbidden:
        raise ValueError(f"Layer 3 output contains forbidden claim(s) outside forbidden_claims: {forbidden}")


@router.post("/reason", response_model=Layer3Result)
async def layer3_reason(payload: FactPackage) -> Layer3Result:
    trace_id = str(uuid4())
    logger.info(
        "layer3.received trace_id=%s endpoint=/api/v1/layer3/reason case_id=%s",
        trace_id,
        payload.case_id,
    )

    def call() -> Layer3Result:
        # Standalone Layer 3: no Layer 1, no Layer 2 — the FactPackage is given.
        return run_layer3(fact_package=payload, trace_id=trace_id)

    try:
        result = await asyncio.to_thread(call)
        _guard_output(result)
        logger.info(
            "layer3.outcome trace_id=%s status=%s",
            trace_id,
            result.status.value,
        )
        return result
    except RuntimeError as exc:
        logger.warning("layer3.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("layer3.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("layer3.error trace_id=%s error=%s", trace_id, _err(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _err(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"
