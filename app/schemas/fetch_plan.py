# app/schemas/fetch_plan.py
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, field_validator

from app.schemas.block_response import _reject_unknown_mode
from app.schemas.shipment_request import RequestedMode as TransportMode


# ---- controlled vocabularies ----
class FetchPriority(str, Enum):
    fail_fast = "fail_fast"      # gate: if this fails/blocks, skip deeper calls for the mode
    required = "required"        # must be called for a usable answer
    optional = "optional"        # planning/enrichment only


class EmptyResponseBehavior(str, Enum):
    # Layer 2 doc Section 16 — "empty != all clear"
    hard_unknown = "hard_unknown"            # serious gap, cannot confirm viability
    soft_unknown = "soft_unknown"            # gap, requires external validation, cap confidence
    planning_unknown = "planning_unknown"    # gap, not a blocker (e.g. cost reference)
    fail_fast_unknown = "fail_fast_unknown"  # empty here stops deeper calls for the mode
    skip_allowed = "skip_allowed"            # empty is genuinely fine here


class FallbackPolicy(str, Enum):
    # Layer 2 doc Section 10 — mock/live fallback, never hidden
    fallback_to_mock = "fallback_to_mock"
    return_unknown = "return_unknown"
    return_planning_only = "return_planning_only"
    fail_request = "fail_request"


# ---- a single required input the block needs to run ----
class RequiredInput(BaseModel):
    field: str                   # dotted path into ValidatedShipmentRequest, e.g. "lane.origin_country"
    reason: str | None = None    # why this block needs it


# ---- one planned connector call ----
class FetchPlanItem(BaseModel):
    block_id: str                # e.g. "ROAD-C"
    mode: TransportMode
    reason: str                  # free-text audit explanation, not an enum by design
    priority: FetchPriority
    required_inputs: list[RequiredInput] = Field(default_factory=list)
    skip_condition: str | None = None  # human-readable only; executor must NOT parse this
    empty_behavior: EmptyResponseBehavior
    fallback_policy: FallbackPolicy

    _check_mode = field_validator("mode")(_reject_unknown_mode)


# ---- the full auditable plan ----
class FetchPlan(BaseModel):
    case_id: str
    items: list[FetchPlanItem] = Field(default_factory=list)