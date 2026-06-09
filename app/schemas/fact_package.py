# app/schemas/fact_package.py
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, field_validator

from app.schemas.shipment_request import ValidatedShipmentRequest, RequestedMode as TransportMode
from app.schemas.fetch_plan import FetchPlan
from app.schemas.block_response import BlockResponse, HardGate, Unknown, _reject_unknown_mode

# BlockResponse owns block-level facts. global_* owns cross-block facts.
# derived_rollup is a regenerated convenience snapshot; do not hand-edit it.


# ---- completeness (Layer 2 doc Section 14) ----
class CompletenessStatus(str, Enum):
    complete_enough = "complete_enough"
    incomplete_but_usable = "incomplete_but_usable"
    insufficient = "insufficient"
    blocked = "blocked"


class Completeness(BaseModel):
    status: CompletenessStatus
    reasons: list[str] = Field(default_factory=list)


# ---- confidence cap (rolled up from blocks + global) ----
class ConfidenceCap(BaseModel):
    cap: float
    reasons: list[str] = Field(default_factory=list)
    source_block: str | None = None   # None => request/global-level cap


# ---- conflict (Layer 2 doc Section 15) ----
class Conflict(BaseModel):
    type: str
    message: str
    action: str | None = None


# ---- derived rollup: regenerated from blocks + global_*, never hand-edited ----
class FactPackageRollup(BaseModel):
    hard_gates: list[HardGate] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    confidence_caps: list[ConfidenceCap] = Field(default_factory=list)
    modes_covered: list[TransportMode] = Field(default_factory=list)
    blocks_called: list[str] = Field(default_factory=list)
    blocks_failed: list[str] = Field(default_factory=list)
    blocks_empty: list[str] = Field(default_factory=list)

    @field_validator("modes_covered")
    @classmethod
    def _no_unknown_modes_covered(cls, v: list[TransportMode]) -> list[TransportMode]:
        for mode in v:
            _reject_unknown_mode(mode)
        return v


# ---- the Layer 2 -> Layer 3 contract ----
class FactPackage(BaseModel):
    case_id: str

    request: ValidatedShipmentRequest
    fetch_plan: FetchPlan
    block_responses: list[BlockResponse] = Field(default_factory=list)

    # cross-block / request-level source of truth (NOT block-level copies)
    global_hard_gates: list[HardGate] = Field(default_factory=list)
    global_unknowns: list[Unknown] = Field(default_factory=list)
    global_missing_fields: list[str] = Field(default_factory=list)

    conflicts: list[Conflict] = Field(default_factory=list)
    completeness: Completeness

    derived_rollup: FactPackageRollup