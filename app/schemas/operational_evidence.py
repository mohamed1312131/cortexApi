from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.shipment_request import RequestedMode


class OperationalEvidenceBase(BaseModel):
    model_config = {"extra": "ignore"}


class EvidenceStatus(str, Enum):
    available = "available"
    planning_reference = "planning_reference"
    requires_validation = "requires_validation"
    not_available = "not_available"
    blocked = "blocked"
    unknown = "unknown"


class RecommendationRole(str, Enum):
    recommended = "recommended"
    fallback = "fallback"
    specialized_study = "specialized_study"
    blocked = "blocked"
    not_evaluated = "not_evaluated"
    supporting_only = "supporting_only"
    unknown = "unknown"


class EvidenceQuality(str, Enum):
    verified = "verified"
    planning_reference = "planning_reference"
    partial = "partial"
    low_data = "low_data"
    not_available = "not_available"
    unknown = "unknown"


class RiskSeverity(str, Enum):
    blocking = "blocking"
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class RiskCategory(str, Enum):
    gateway = "gateway"
    cost = "cost"
    schedule = "schedule"
    documents = "documents"
    cargo_fit = "cargo_fit"
    compliance = "compliance"
    carrier = "carrier"
    customs = "customs"
    handling = "handling"
    road_corridor = "road_corridor"
    data_gap = "data_gap"
    other = "other"


class RouteLegType(str, Enum):
    first_mile = "first_mile"
    main_leg = "main_leg"
    last_mile = "last_mile"
    border_transit = "border_transit"
    unknown = "unknown"


class EvidenceSourceRef(OperationalEvidenceBase):
    block_id: str | None = None
    mode: RequestedMode | None = None
    field_path: str | None = None
    record_id: str | None = None
    source: str | None = None
    provider_used: str | None = None


class GatewayEvidence(OperationalEvidenceBase):
    status: EvidenceStatus = EvidenceStatus.unknown
    origin_candidates: list[str] = Field(default_factory=list)
    destination_candidates: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    requires_validation: list[str] = Field(default_factory=list)
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class RouteLegEvidence(OperationalEvidenceBase):
    leg_type: RouteLegType = RouteLegType.unknown
    mode: RequestedMode
    origin: str | None = None
    destination: str | None = None
    status: EvidenceStatus = EvidenceStatus.unknown
    assumptions: list[str] = Field(default_factory=list)
    requires_validation: list[str] = Field(default_factory=list)
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class CostEstimate(OperationalEvidenceBase):
    low: float | None = None
    typical: float | None = None
    high: float | None = None


class CostBoundaryEvidence(OperationalEvidenceBase):
    status: EvidenceStatus = EvidenceStatus.unknown
    currency: str | None = None
    estimate: CostEstimate | None = None
    basis: str | None = None
    included_items: list[str] = Field(default_factory=list)
    excluded_items: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class TransitTimeEstimate(OperationalEvidenceBase):
    low_days: float | None = None
    typical_days: float | None = None
    high_days: float | None = None


class ScheduleBoundaryEvidence(OperationalEvidenceBase):
    status: EvidenceStatus = EvidenceStatus.unknown
    ready_date: str | None = None
    deadline: str | None = None
    transit_time: TransitTimeEstimate | None = None
    feasibility_statement: str | None = None
    deadline_fit: str | None = None
    requires_live_schedule: bool = True
    limitations: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class DocumentEvidence(OperationalEvidenceBase):
    status: EvidenceStatus = EvidenceStatus.unknown
    required_documents: list[str] = Field(default_factory=list)
    conditional_documents: list[str] = Field(default_factory=list)
    missing_or_unconfirmed: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class HandlingSafetyEvidence(OperationalEvidenceBase):
    status: EvidenceStatus = EvidenceStatus.unknown
    requirements: list[str] = Field(default_factory=list)
    cargo_fit_notes: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class OperationalRiskEvidence(OperationalEvidenceBase):
    category: RiskCategory = RiskCategory.other
    severity: RiskSeverity = RiskSeverity.unknown
    message: str
    mitigation: str | None = None
    source_refs: list[EvidenceSourceRef] = Field(default_factory=list)


class OperationalPathEvidence(OperationalEvidenceBase):
    path_family_id: str
    rank: int | None = None
    primary_mode: RequestedMode
    leg_modes: list[RequestedMode] = Field(default_factory=list)
    display_name: str
    recommendation_role: RecommendationRole = RecommendationRole.unknown
    status: EvidenceStatus = EvidenceStatus.unknown
    readiness_band: str | None = None
    confidence_band: str | None = None
    evidence_quality: EvidenceQuality = EvidenceQuality.unknown
    route_legs: list[RouteLegEvidence] = Field(default_factory=list)
    gateways: GatewayEvidence | None = None
    cost: CostBoundaryEvidence | None = None
    schedule: ScheduleBoundaryEvidence | None = None
    documents: DocumentEvidence | None = None
    handling_safety: HandlingSafetyEvidence | None = None
    blockers: list[OperationalRiskEvidence] = Field(default_factory=list)
    risks: list[OperationalRiskEvidence] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class OperationalEvidence(OperationalEvidenceBase):
    case_id: str
    evidence_version: str = "operational_evidence.v1"
    generated_from: dict[str, str | None] = Field(default_factory=dict)
    shipment: dict[str, Any] = Field(default_factory=dict)
    paths: list[OperationalPathEvidence] = Field(default_factory=list)
    global_blockers: list[OperationalRiskEvidence] = Field(default_factory=list)
    global_unknowns: list[str] = Field(default_factory=list)
    global_limitations: list[str] = Field(default_factory=list)
