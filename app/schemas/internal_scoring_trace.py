# app/schemas/internal_scoring_trace.py
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.shipment_request import RequestedMode
from app.schemas.reasoning_decision import ReadinessBand

# INTERNAL ONLY. This file is the home for raw numeric scoring.
# It is linked to a ReasoningDecision by case_id + reasoning_decision_id, but it
# is NEVER embedded in ReasoningDecision and NEVER crosses into Layer 4.
# Do not export these models from app/schemas/__init__.py.


class ScoringStep(BaseModel):
    step_name: str
    path_family: str | None = None
    mode: RequestedMode | None = None
    input_refs: list[str] = Field(default_factory=list)
    raw_score: float | None = None
    applied_cap: str | None = None
    resulting_band: ReadinessBand | None = None
    reason: str = ""


class InternalScoringTrace(BaseModel):
    case_id: str
    trace_id: str | None = None
    reasoning_decision_id: str | None = None
    steps: list[ScoringStep] = Field(default_factory=list)
    raw_scores_by_path: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
