from __future__ import annotations

from typing import TypedDict

from app.schemas.fact_package import FactPackage
from app.schemas.internal_scoring_trace import InternalScoringTrace
from app.schemas.layer3 import (
    AnalystDraft,
    CriticReview,
    DeterministicDecision,
    Layer3NextAction,
    Layer3Result,
    ReasoningContext,
    SafetyGateReport,
)
from app.schemas.reasoning_decision import ReasoningDecision
from app.schemas.shipment_request import ValidatedShipmentRequest


class Layer3State(TypedDict, total=False):
    case_id: str
    request: ValidatedShipmentRequest
    fact_package: FactPackage

    reasoning_context: ReasoningContext
    deterministic_decision: DeterministicDecision
    internal_scoring_trace: InternalScoringTrace

    analyst_draft: AnalystDraft | None
    analyst_error: str | None
    analyst_revision_feedback: str | None
    blocked_reasoning_decision_error: str | None
    critic_review: CriticReview
    safety_gate_report: SafetyGateReport

    reasoning_decision: ReasoningDecision
    result: Layer3Result

    revision_count: int
    max_revisions: int
    agent_run_order: int
    next_action: Layer3NextAction
    trace_id: str | None
    conversation_id: str | None
