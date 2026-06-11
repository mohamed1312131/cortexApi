from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.shipment_request import QuestionToUser, ValidatedShipmentRequest


class CaseAction(str, Enum):
    create_new_case = "create_new_case"
    update_existing_case = "update_existing_case"
    answer_intake_question = "answer_intake_question"
    clarify_missing_field = "clarify_missing_field"
    ask_detail_about_existing_report = "ask_detail_about_existing_report"
    compare_mode_request = "compare_mode_request"
    change_mode = "change_mode"
    filter_existing_report = "filter_existing_report"
    start_new_case = "start_new_case"
    unknown = "unknown"


class IntakeIntent(str, Enum):
    shipment_readiness = "shipment_readiness"
    best_mode_selection = "best_mode_selection"
    mode_comparison = "mode_comparison"
    document_check = "document_check"
    cost_planning = "cost_planning"
    timing_planning = "timing_planning"
    risk_check = "risk_check"
    follow_up_update = "follow_up_update"
    ask_explanation = "ask_explanation"
    unknown = "unknown"


class CaseStatus(str, Enum):
    intake_in_progress = "intake_in_progress"
    ready_for_layer_2 = "ready_for_layer_2"
    waiting_for_user_clarification = "waiting_for_user_clarification"
    case_closed = "case_closed"


class IntakeDecision(str, Enum):
    ask_user = "ask_user"
    ready_for_layer_2 = "ready_for_layer_2"
    ready_for_layer_2_with_unknowns = "ready_for_layer_2_with_unknowns"
    answer_user_explanation = "answer_user_explanation"
    update_case_and_rerun = "update_case_and_rerun"
    start_new_case = "start_new_case"


class FieldSourceType(str, Enum):
    provided_by_user = "provided_by_user"
    inferred_from_user_text = "inferred_from_user_text"
    retrieved_from_memory_pattern = "retrieved_from_memory_pattern"
    system_default = "system_default"
    unknown = "unknown"


class CaseState(BaseModel):
    case_id: str
    conversation_id: str | None = None
    user_id: str | None = None
    company_id: str | None = None
    status: CaseStatus = CaseStatus.intake_in_progress
    shipment_request_version: int = 0
    current_shipment_request: ValidatedShipmentRequest | None = None
    conversation_summary: str = ""
    last_missing_questions: list[str] = Field(default_factory=list)
    user_corrections: list[dict[str, Any]] = Field(default_factory=list)
    active_profiles: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IntakeMessageRequest(BaseModel):
    conversation_id: str | None = None
    case_id: str | None = None
    user_id: str | None = None
    company_id: str | None = None
    message: str


class IntakeResult(BaseModel):
    conversation_id: str | None = None
    case_id: str
    case_action: CaseAction
    intent: IntakeIntent
    decision: IntakeDecision
    assistant_message: str
    intake_json: ValidatedShipmentRequest | None = None
    ready_for_layer_2: bool = False
    requires_layer_2_rerun: bool = False
    changed_fields: list[str] = Field(default_factory=list)
    rerun_scope: dict[str, Any] = Field(default_factory=dict)
    questions_to_user: list[QuestionToUser] = Field(default_factory=list)
