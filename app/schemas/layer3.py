# app/schemas/layer3.py
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.shipment_request import RequestedMode
from app.schemas.block_response import _reject_unknown_mode
from app.schemas.reasoning_decision import (
    ConfidenceReport,
    MustShowWarning,
    ReadinessBand,
    ReasoningDecision,
    RankingType,
)

# Layer 3 internal + envelope schemas.
#
# DeterministicDecision is the internal ranking truth. The Analyst only explains
# it; the Critic validates the Analyst; the code safety gate is final. None of
# these internal models carry raw numeric scores — those live exclusively in
# internal_scoring_trace.InternalScoringTrace, referenced here by id only.
#
# ReasoningDecision (the frozen Layer 3 -> Layer 4 seam) is imported and reused,
# never redefined.


# ---- helper: optional concrete-mode guard ----
def _reject_unknown_mode_optional(v: RequestedMode | None) -> RequestedMode | None:
    if v is not None:
        _reject_unknown_mode(v)
    return v


def _reject_unknown_modes(v: list[RequestedMode]) -> list[RequestedMode]:
    for mode in v:
        _reject_unknown_mode(mode)
    return v


# ---- enums ----
class Layer3Status(str, Enum):
    pass_to_layer4 = "pass_to_layer4"
    request_user_clarification = "request_user_clarification"
    request_layer2_fetch = "request_layer2_fetch"
    blocked = "blocked"
    error = "error"


class Layer3NextAction(str, Enum):
    pass_to_layer4 = "pass_to_layer4"
    revise_analyst = "revise_analyst"
    request_user_clarification = "request_user_clarification"
    request_layer2_fetch = "request_layer2_fetch"
    block_unsafe = "block_unsafe"


class CriticVerdict(str, Enum):
    pass_ = "pass"
    revise = "revise"
    block = "block"
    skipped = "skipped"


class SafetyGateStatus(str, Enum):
    pass_ = "pass"
    revise = "revise"
    block = "block"


# ---- A. EvidenceRef ----
class EvidenceRef(BaseModel):
    ref_id: str
    source_type: str
    source_block: str | None = None
    mode: RequestedMode | None = None
    field_path: str | None = None
    basis: str | None = None

    _check_mode = field_validator("mode")(_reject_unknown_mode_optional)


# ---- B. ReasoningFactor ----
class ReasoningFactor(BaseModel):
    code: str
    label: str
    severity: str
    mode: RequestedMode | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    details: str | None = None
    # For hard gates, carries the Layer 2 HardGate.status value (e.g. "triggered",
    # "not_triggered", "unknown") so the deterministic engine never has to parse
    # `details`. None for unknowns/missing fields/conflicts unless a status exists.
    status: str | None = None

    _check_mode = field_validator("mode")(_reject_unknown_mode_optional)


# ---- C. ReasoningContext (read-model derived from FactPackage; no LLM fields) ----
class ReasoningContext(BaseModel):
    case_id: str
    request_summary: dict = Field(default_factory=dict)
    candidate_modes: list[RequestedMode] = Field(default_factory=list)
    active_profiles: list[str] = Field(default_factory=list)
    modes_covered: list[RequestedMode] = Field(default_factory=list)
    block_statuses: dict[str, str] = Field(default_factory=dict)
    hard_gates: list[ReasoningFactor] = Field(default_factory=list)
    unknowns: list[ReasoningFactor] = Field(default_factory=list)
    missing_fields: list[ReasoningFactor] = Field(default_factory=list)
    conflicts: list[ReasoningFactor] = Field(default_factory=list)
    confidence_cap_reasons: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    completeness_status: str | None = None

    _check_candidate_modes = field_validator("candidate_modes")(_reject_unknown_modes)
    _check_modes_covered = field_validator("modes_covered")(_reject_unknown_modes)


# ---- D. RankedPathFamilyDecision ----
class RankedPathFamilyDecision(BaseModel):
    rank: int
    path_family: str
    mode: RequestedMode
    readiness_band: ReadinessBand
    ranking_type: RankingType
    evidence_refs: list[str]
    applied_caps: list[str] = Field(default_factory=list)
    blocking_factors: list[str] = Field(default_factory=list)
    unknown_factors: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    _check_mode = field_validator("mode")(_reject_unknown_mode)

    @field_validator("rank")
    @classmethod
    def _rank_at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError("rank must be >= 1")
        return v

    @field_validator("evidence_refs")
    @classmethod
    def _evidence_refs_required(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("evidence_refs must not be empty")
        return v


# ---- E. DeterministicDecision (internal; no raw scores) ----
class DeterministicDecision(BaseModel):
    case_id: str
    overall_readiness_band: ReadinessBand
    ranking_type: RankingType
    ranked_path_families: list[RankedPathFamilyDecision] = Field(default_factory=list)
    hard_gate_summary: list[ReasoningFactor] = Field(default_factory=list)
    critical_unknowns: list[ReasoningFactor] = Field(default_factory=list)
    confidence_report: ConfidenceReport
    must_show_warnings: list[MustShowWarning] = Field(default_factory=list)
    internal_trace_ref: str | None = None

    @model_validator(mode="after")
    def _non_empty_unless_blocked(self) -> "DeterministicDecision":
        if (
            self.overall_readiness_band is not ReadinessBand.BLOCKED
            and not self.ranked_path_families
        ):
            raise ValueError(
                "ranked_path_families must be non-empty unless overall_readiness_band is BLOCKED"
            )
        return self


# ---- F. AnalystPathNarrative ----
class AnalystPathNarrative(BaseModel):
    path_family: str
    mode: RequestedMode
    rank: int
    why_ranked_here: str
    why_not_higher: str
    what_would_improve_readiness: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)

    _check_mode = field_validator("mode")(_reject_unknown_mode)

    @field_validator("rank")
    @classmethod
    def _rank_at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError("rank must be >= 1")
        return v


# ---- G. AnalystDraft (explains DeterministicDecision; never re-ranks) ----
class AnalystDraft(BaseModel):
    case_id: str
    narratives: list[AnalystPathNarrative] = Field(default_factory=list)
    overall_summary: str
    next_action_summary: str | None = None
    user_clarification_questions: list[str] = Field(default_factory=list)
    layer2_refetch_requests: list[str] = Field(default_factory=list)
    disputes_ranking: bool = False
    dispute_reason: str | None = None
    forbidden_claims_used: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _dispute_requires_reason(self) -> "AnalystDraft":
        if self.disputes_ranking and not (self.dispute_reason and self.dispute_reason.strip()):
            raise ValueError("dispute_reason must be populated when disputes_ranking is True")
        return self


# ---- H. CriticFinding ----
class CriticFinding(BaseModel):
    code: str
    severity: str
    message: str
    evidence_refs: list[str] = Field(default_factory=list)
    required_change: str | None = None


# ---- I. CriticReview ----
class CriticReview(BaseModel):
    verdict: CriticVerdict
    findings: list[CriticFinding] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    hidden_hard_gates: list[str] = Field(default_factory=list)
    hidden_unknowns: list[str] = Field(default_factory=list)
    contradiction_with_deterministic_ranking: bool = False
    required_changes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verdict_consistency(self) -> "CriticReview":
        if self.verdict in (CriticVerdict.revise, CriticVerdict.block) and not self.findings:
            raise ValueError("findings must not be empty when verdict is revise or block")
        if self.contradiction_with_deterministic_ranking and self.verdict is CriticVerdict.pass_:
            raise ValueError(
                "verdict cannot be pass when contradiction_with_deterministic_ranking is True"
            )
        return self


# ---- J. SafetyViolation ----
class SafetyViolation(BaseModel):
    code: str
    severity: str
    message: str
    field_path: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


# ---- K. SafetyGateReport (Python; authoritative) ----
class SafetyGateReport(BaseModel):
    status: SafetyGateStatus
    violations: list[SafetyViolation] = Field(default_factory=list)
    passed: bool
    next_action: Layer3NextAction

    @model_validator(mode="after")
    def _status_consistency(self) -> "SafetyGateReport":
        if self.status is SafetyGateStatus.pass_:
            if not self.passed:
                raise ValueError("status=pass implies passed=True")
            if self.next_action not in (
                Layer3NextAction.pass_to_layer4,
                Layer3NextAction.request_user_clarification,
                Layer3NextAction.request_layer2_fetch,
            ):
                raise ValueError("status=pass requires a non-blocking next_action")
        elif self.status is SafetyGateStatus.revise:
            if self.passed:
                raise ValueError("status=revise implies passed=False")
            if self.next_action is not Layer3NextAction.revise_analyst:
                raise ValueError("status=revise requires next_action=revise_analyst")
        elif self.status is SafetyGateStatus.block:
            if self.passed:
                raise ValueError("status=block implies passed=False")
            if self.next_action is not Layer3NextAction.block_unsafe:
                raise ValueError("status=block requires next_action=block_unsafe")
        return self


# ---- L. Layer3Result (service/dev envelope; NOT the Layer 4 customer output) ----
class Layer3Result(BaseModel):
    case_id: str
    status: Layer3Status
    reasoning_decision: ReasoningDecision | None = None
    analyst_draft: AnalystDraft | None = None
    critic_review: CriticReview | None = None
    safety_gate_report: SafetyGateReport | None = None
    debug: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _pass_requires_decision(self) -> "Layer3Result":
        if self.status is Layer3Status.pass_to_layer4 and self.reasoning_decision is None:
            raise ValueError("reasoning_decision must not be None when status is pass_to_layer4")
        return self
