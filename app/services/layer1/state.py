from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel

from app.schemas import (
    CaseAction,
    CaseState,
    IntakeDecision,
    IntakeIntent,
    IntakeResult,
    ValidatedShipmentRequest,
)


class RouteDecision(BaseModel):
    case_action: CaseAction = CaseAction.unknown
    intent: IntakeIntent = IntakeIntent.unknown
    requires_extraction: bool = False
    requires_case_update: bool = False
    target: str | None = None
    confidence: float = 0.0
    reason: str = ""


class ValidationIssue(BaseModel):
    field: str
    reason: str


class IntakeGraphState(TypedDict, total=False):
    conversation_id: str | None
    case_id: str | None
    user_id: str | None
    company_id: str | None
    message: str

    case_context: CaseState | None
    has_active_case: bool
    memory_hints: list[dict[str, Any]]
    memory_hints_used: list[dict[str, Any]]

    route: RouteDecision
    case_state: CaseState
    previous_request: ValidatedShipmentRequest | None
    current_request: ValidatedShipmentRequest | None
    changed_fields: list[str]
    multiple_shipments_detected: bool

    validation_errors: list[ValidationIssue]
    validation_warnings: list[ValidationIssue]
    rejected_fields: list[ValidationIssue]

    decision: IntakeDecision
    assistant_message: str
    rerun_scope: dict[str, Any]
    requires_layer_2_rerun: bool
    result: IntakeResult
