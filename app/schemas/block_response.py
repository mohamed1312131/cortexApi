# app/schemas/block_response.py
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator

from app.schemas.shipment_request import RequestedMode as TransportMode
# One mode vocabulary across the whole system. TransportMode includes `unknown`,
# which is valid for a user's requested_mode but NOT for a concrete connector
# result or a hard gate — enforced by _reject_unknown_mode below.


def _reject_unknown_mode(v: TransportMode) -> TransportMode:
    if v is TransportMode.unknown:
        raise ValueError("mode must be a concrete mode (sea/air/road), not 'unknown'")
    return v


# ---- block status (Layer 2 doc, Section 13) ----
class BlockStatus(str, Enum):
    found = "found"
    not_found = "not_found"
    unknown = "unknown"
    skipped = "skipped"
    error = "error"
    not_applicable = "not_applicable"


# ---- hard gates ----
class GateSeverity(str, Enum):
    blocking = "blocking"
    high = "high"
    medium = "medium"
    low = "low"


class GateStatus(str, Enum):
    triggered = "triggered"
    not_triggered = "not_triggered"
    unknown = "unknown"


class HardGate(BaseModel):
    gate_id: str
    mode: TransportMode
    severity: GateSeverity
    status: GateStatus
    message: str
    source_block: str
    basis: str | None = None

    _check_mode = field_validator("mode")(_reject_unknown_mode)


# ---- confidence (Layer 2 doc Section 13) ----
class SourceConfidence(str, Enum):
    verified = "verified"
    estimated = "estimated"
    authored = "authored"
    planning_reference = "planning_reference"
    unknown = "unknown"


class BlockConfidence(BaseModel):
    source_confidence: SourceConfidence = SourceConfidence.unknown
    cap: float | None = None
    reasons: list[str] = Field(default_factory=list)


# ---- unknowns (typed — load-bearing for the honest dossier) ----
class Unknown(BaseModel):
    field: str
    reason: str
    impact: str | None = None


# ---- provider transparency (Layer 2 doc Section 10) ----
class ProviderUsed(str, Enum):
    mock = "mock"
    live = "live"


# ---- provenance (Layer 2 doc Section 13 + Section 10 mock/live transparency) ----
class Provenance(BaseModel):
    source: str
    record_id: str | None = None
    provider_used: ProviderUsed = ProviderUsed.mock
    fallback_used: bool = False
    live_data_available: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


# ---- the normalized connector envelope ----
class BlockResponse(BaseModel):
    block_id: str
    mode: TransportMode
    status: BlockStatus

    data: dict[str, Any] = Field(default_factory=dict)        # LOOSE — connector-specific payload

    hard_gates: list[HardGate] = Field(default_factory=list)  # TYPED — safety-critical
    planning_factors: list[str] = Field(default_factory=list)
    unknowns: list[Unknown] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    confidence: BlockConfidence = Field(default_factory=BlockConfidence)
    provenance: Provenance

    _check_mode = field_validator("mode")(_reject_unknown_mode)