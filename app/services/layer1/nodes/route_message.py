from __future__ import annotations

from app.schemas import CaseAction
from app.services.layer1.intent_classifier import classify_intent
from app.services.layer1.message_router import route_message
from app.services.layer1.state import IntakeGraphState, RouteDecision


def route_message_node(state: IntakeGraphState) -> IntakeGraphState:
    message = state.get("message", "")
    case_context = state.get("case_context")
    case_action = route_message(message, case_context)
    intent = classify_intent(message, case_action)
    route = RouteDecision(
        case_action=case_action,
        intent=intent,
        requires_extraction=_requires_extraction(case_action),
        requires_case_update=_requires_case_update(case_action),
        confidence=0.8 if case_action is not CaseAction.unknown else 0.25,
        reason=_route_reason(case_action),
    )
    return {**state, "route": route}


def _requires_extraction(action: CaseAction) -> bool:
    return action in {
        CaseAction.create_new_case,
        CaseAction.update_existing_case,
        CaseAction.clarify_missing_field,
        CaseAction.compare_mode_request,
        CaseAction.change_mode,
        CaseAction.start_new_case,
    }


def _requires_case_update(action: CaseAction) -> bool:
    return action in {
        CaseAction.create_new_case,
        CaseAction.update_existing_case,
        CaseAction.clarify_missing_field,
        CaseAction.compare_mode_request,
        CaseAction.change_mode,
        CaseAction.start_new_case,
    }


def _route_reason(action: CaseAction) -> str:
    reasons = {
        CaseAction.create_new_case: "User appears to be starting shipment intake.",
        CaseAction.update_existing_case: "User appears to be updating the active shipment case.",
        CaseAction.clarify_missing_field: "User appears to be answering an intake clarification.",
        CaseAction.answer_intake_question: "User asked for an intake concept explanation.",
        CaseAction.ask_detail_about_existing_report: "User asked about an existing report or recommendation.",
        CaseAction.compare_mode_request: "User asked to compare or consider another mode.",
        CaseAction.change_mode: "User asked to change the requested transport mode.",
        CaseAction.filter_existing_report: "User asked to filter an existing report view.",
        CaseAction.start_new_case: "User explicitly asked to start a different shipment case.",
        CaseAction.unknown: "Message could not be safely mapped to shipment intake.",
    }
    return reasons[action]
