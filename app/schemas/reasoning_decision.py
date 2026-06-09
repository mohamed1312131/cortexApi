# app/schemas/reasoning_decision.py
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, field_validator

from app.schemas.shipment_request import RequestedMode as TransportMode
from app.schemas.block_response import HardGate, Unknown, _reject_unknown_mode

# ReasoningDecision is user-safe BY CONSTRUCTION. It contains NO raw numeric scores.
# Only bands, explanations, warnings, claims, and ranked preparation options.
# Internal numeric scoring lives in internal_scoring_trace.py, linked by case_id +
# reasoning_decision_id, and NEVER crosses into Layer 4.


# ---- readiness bands (Layer 3 doc Section 13) ----
class ReadinessBand(str, Enum):
    BLOCKED = "BLOCKED"
    SPECIALIZED_STUDY_REQUIRED = "SPECIALIZED_STUDY_REQUIRED"
    LOW = "LOW"
    MEDIUM_LOW = "MEDIUM_LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ---- ranking type (Layer 3 doc Section 11) ----
class RankingType(str, Enum):
    preparation_ranking = "preparation_ranking"
    screening_ranking = "screening_ranking"
    low_data_ranking = "low_data_ranking"
    blocked_ranking = "blocked_ranking"
    booking_ranking = "booking_ranking"


# ---- confidence as a BAND, with reasons (the locked hybrid rule) ----
class ConfidenceBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConfidenceReport(BaseModel):
    band: ConfidenceBand
    cap_reasons: list[str] = Field(default_factory=list)   # why confidence is capped
    # NOTE: no numeric value by design. The number lives in the internal trace.


# ---- one ranked preparation path family (Layer 3 doc Section 16/17) ----
class RankedReadinessOption(BaseModel):
    rank: int
    path_family_id: str
    mode: TransportMode
    readiness_band: ReadinessBand
    status: str  # display/status label only; Layer 4 must not branch on this
    why_ranked_here: str
    why_not_higher: str                      # mandatory per Section 16
    hard_gates: list[HardGate] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    _check_mode = field_validator("mode")(_reject_unknown_mode)

    @field_validator("rank")
    @classmethod
    def _rank_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("rank must be positive")
        return v


# ---- a warning Layer 4 must surface (Layer 3/4 must-show) ----
class MustShowWarning(BaseModel):
    code: str                                # e.g. "NOT_A_BOOKING_CONFIRMATION"
    message: str


# ---- the Layer 3 -> Layer 4 contract ----
class ReasoningDecision(BaseModel):
    case_id: str
    reasoning_decision_id: str               # links to internal_scoring_trace, audit

    ranking_type: RankingType
    ranked_readiness_options: list[RankedReadinessOption] = Field(default_factory=list)

    confidence: ConfidenceReport

    allowed_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)

    global_unknowns: list[Unknown] = Field(default_factory=list)
    global_next_actions: list[str] = Field(default_factory=list)

    must_show_warnings: list[MustShowWarning] = Field(default_factory=list)