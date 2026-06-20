from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.repositories.agent_run_repository import SqlAlchemyAgentRunRepository
from app.schemas.agent_trace import AgentRunsResponse


router = APIRouter(prefix="/api/v1/cortex", tags=["cortex-debug"])
logger = get_logger(__name__)
_REPOSITORY = SqlAlchemyAgentRunRepository()


@router.get("/cases/{case_id}/agent-runs", response_model=AgentRunsResponse)
async def cortex_case_agent_runs(case_id: str) -> AgentRunsResponse:
    try:
        runs = await _REPOSITORY.list_by_case(case_id)
    except Exception as exc:
        logger.warning(
            "agent_runs_endpoint.error case_id=%s error=%s: %s",
            case_id,
            exc.__class__.__name__,
            exc,
        )
        raise HTTPException(status_code=503, detail="Agent run trace storage is unavailable.") from exc
    return AgentRunsResponse(case_id=case_id, runs=runs)
