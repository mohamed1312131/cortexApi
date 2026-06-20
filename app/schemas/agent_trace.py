from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentRunStatus(str, Enum):
    success = "success"
    error = "error"
    skipped = "skipped"


class AgentRunRecord(BaseModel):
    id: str
    case_id: str
    conversation_id: str | None = None
    trace_id: str | None = None
    layer: int
    agent_name: str
    run_order: int
    status: AgentRunStatus
    model_name: str | None = None
    provider: str | None = None
    prompt_chars: int = 0
    prompt_rough_tokens: int = 0
    response_chars: int = 0
    response_rough_tokens: int = 0
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] | list[Any] | None = None
    safety_report: dict[str, Any] | None = None
    error_message: str | None = None
    prompt_artifact_ref: str | None = None
    response_artifact_ref: str | None = None
    started_at: datetime
    ended_at: datetime


class AgentRunsResponse(BaseModel):
    case_id: str
    runs: list[AgentRunRecord] = Field(default_factory=list)
