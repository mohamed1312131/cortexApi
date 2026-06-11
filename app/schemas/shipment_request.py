# app/schemas/shipment_request.py
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


# ---- enums ----
class FlagState(str, Enum):
    yes = "yes"
    no = "no"
    likely = "likely"
    unknown = "unknown"


class RequestedMode(str, Enum):
    sea = "sea"
    air = "air"
    road = "road"
    unknown = "unknown"


class Priority(str, Enum):
    cost = "cost"
    speed = "speed"
    risk = "risk"
    compliance = "compliance"
    balanced = "balanced"
    unknown = "unknown"


# ---- nested groups ----
class UserGoal(BaseModel):
    primary_goal: str = "find_preparation_paths"
    priority: Priority = Priority.unknown
    deadline_sensitivity: str = "unknown"


class CoreShipment(BaseModel):
    cargo_description: str | None = None
    weight_kg: float | None = None
    volume_cbm: float | None = None
    dimensions: list[float] | None = None   # [L, W, H] when known
    quantity: int | None = None
    packaging: str | None = None

    @field_validator("dimensions")
    @classmethod
    def _dimensions_triple(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        if len(v) != 3:
            raise ValueError("dimensions must be None or exactly [L, W, H]")
        if any(d <= 0 for d in v):
            raise ValueError("dimensions must be positive")
        return v


class Lane(BaseModel):
    origin_raw: str | None = None
    destination_raw: str | None = None
    origin_country: str | None = None        # ISO-2, e.g. "CN"
    destination_country: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None


class ModeSelection(BaseModel):
    requested_mode: RequestedMode = RequestedMode.unknown
    candidate_modes: list[RequestedMode] = Field(
        default_factory=lambda: [RequestedMode.sea, RequestedMode.air, RequestedMode.road]
    )
    needs_mode_selection: bool = True

    @field_validator("candidate_modes")
    @classmethod
    def _no_unknown_in_candidates(cls, v: list[RequestedMode]) -> list[RequestedMode]:
        if RequestedMode.unknown in v:
            raise ValueError("candidate_modes cannot contain 'unknown'")
        return v


class CargoFlags(BaseModel):
    dangerous_goods: FlagState = FlagState.unknown
    temperature_controlled: FlagState = FlagState.unknown
    oversized: FlagState = FlagState.unknown
    high_value: FlagState = FlagState.unknown
    pharma: FlagState = FlagState.unknown
    food_perishable: FlagState = FlagState.unknown
    live_animals: FlagState = FlagState.unknown


class Commercial(BaseModel):
    incoterm: str | None = None
    cargo_value: float | None = None
    currency: str | None = None
    ready_date: str | None = None            # ISO date string
    deadline: str | None = None


class MissingFields(BaseModel):
    blocking: list[str] = Field(default_factory=list)
    high_value: list[str] = Field(default_factory=list)
    can_wait: list[str] = Field(default_factory=list)


class QuestionToUser(BaseModel):
    question: str
    reason: str
    field_target: str


# ---- the frozen contract: Layer 1 -> Layer 2 seam (Shape A, nested) ----
class ValidatedShipmentRequest(BaseModel):
    case_id: str                              # required; API endpoint mints it before construction

    user_goal: UserGoal = Field(default_factory=UserGoal)
    core_shipment: CoreShipment = Field(default_factory=CoreShipment)
    lane: Lane = Field(default_factory=Lane)
    mode: ModeSelection = Field(default_factory=ModeSelection)
    cargo_flags: CargoFlags = Field(default_factory=CargoFlags)

    active_profiles: list[str] = Field(default_factory=list)
    profiles: dict[str, Any] = Field(default_factory=dict)   # dynamic; validated in L2 input guardrails

    commercial: Commercial = Field(default_factory=Commercial)

    facts_from_user: dict[str, Any] = Field(default_factory=dict)
    inferred_flags: dict[str, Any] = Field(default_factory=dict)

    missing_fields: MissingFields = Field(default_factory=MissingFields)
    questions_to_user: list[QuestionToUser] = Field(default_factory=list)

    ready_for_layer_2: bool = False
    field_confidence: dict[str, float] = Field(default_factory=dict)
    intake_quality_score: float = 0.0
