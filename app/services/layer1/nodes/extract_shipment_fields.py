from __future__ import annotations

from app.schemas import CaseAction, CaseState, RequestedMode, ValidatedShipmentRequest
from app.services.layer1.case_state_manager import append_message_to_summary, merge_requests, new_case_id
from app.services.layer1.deterministic_update_extractor import apply_deterministic_updates
from app.services.layer1.extractor import MultipleShipmentDetected, extract_shipment
from app.services.layer1.multi_shipment import build_multiple_shipment_request
from app.services.layer1.state import IntakeGraphState


def extract_shipment_fields(state: IntakeGraphState) -> IntakeGraphState:
    route = state["route"]
    message = state.get("message", "")
    case_state = _working_case_state(state)
    append_message_to_summary(case_state, message)

    if not route.requires_extraction or route.case_action in {
        CaseAction.answer_intake_question,
        CaseAction.ask_detail_about_existing_report,
        CaseAction.filter_existing_report,
        CaseAction.unknown,
    }:
        return {
            **state,
            "case_state": case_state,
            "previous_request": case_state.current_shipment_request,
            "current_request": case_state.current_shipment_request,
            "changed_fields": [],
        }

    previous_request = case_state.current_shipment_request
    if route.case_action is CaseAction.compare_mode_request and previous_request is not None:
        current = previous_request.model_copy(deep=True)
        changed_fields: list[str] = []
    else:
        try:
            incoming = extract_shipment(case_state.case_id, message)
        except MultipleShipmentDetected:
            return {
                **state,
                "case_state": case_state,
                "previous_request": previous_request,
                "current_request": build_multiple_shipment_request(case_state.case_id),
                "changed_fields": [],
                "multiple_shipments_detected": True,
            }
        current, changed_fields = merge_requests(previous_request, incoming)

    current, mode_changed_fields = _apply_mode_follow_up(
        current,
        message,
        route.case_action,
        previous_request,
    )
    changed_fields.extend(mode_changed_fields)

    current, deterministic_changed_fields = apply_deterministic_updates(current, message)
    changed_fields.extend(deterministic_changed_fields)

    return {
        **state,
        "case_state": case_state,
        "previous_request": previous_request,
        "current_request": current,
        "changed_fields": _dedupe(changed_fields),
    }


def _working_case_state(state: IntakeGraphState) -> CaseState:
    route = state["route"]
    existing = state.get("case_context")
    if existing is not None and route.case_action is not CaseAction.start_new_case:
        return existing
    return CaseState(
        case_id=state.get("case_id") or new_case_id(),
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
        company_id=state.get("company_id"),
    )


def _apply_mode_follow_up(
    request: ValidatedShipmentRequest,
    message: str,
    action: CaseAction,
    previous_request: ValidatedShipmentRequest | None,
) -> tuple[ValidatedShipmentRequest, list[str]]:
    mode = _mode_from_text(message)
    if mode is RequestedMode.unknown:
        return request, []

    changed_fields: list[str] = []
    updated = request.model_copy(deep=True)

    if action is CaseAction.compare_mode_request:
        previous_mode = updated.mode.requested_mode
        updated.mode.requested_mode = RequestedMode.unknown
        updated.mode.needs_mode_selection = True
        if updated.facts_from_user.get("requested_mode") == mode.value:
            updated.facts_from_user.pop("requested_mode", None)
            updated.field_confidence.pop("requested_mode", None)
        if previous_request is not None:
            updated.mode.candidate_modes = list(previous_request.mode.candidate_modes)
        if previous_mode is not RequestedMode.unknown:
            changed_fields.append("mode.requested_mode")
        if mode not in updated.mode.candidate_modes:
            updated.mode.candidate_modes.append(mode)
            changed_fields.append("mode.candidate_modes")
        return updated, changed_fields

    if action is CaseAction.change_mode:
        if updated.mode.requested_mode != mode:
            updated.mode.requested_mode = mode
            updated.mode.candidate_modes = [mode]
            updated.mode.needs_mode_selection = False
            updated.facts_from_user["requested_mode"] = mode.value
            updated.field_confidence["requested_mode"] = 0.9
            changed_fields.extend(["mode.requested_mode", "mode.candidate_modes"])
        return updated, changed_fields

    return request, []


def _mode_from_text(message: str) -> RequestedMode:
    text = message.lower()
    if "air" in text or "flight" in text:
        return RequestedMode.air
    if "sea" in text or "ocean" in text:
        return RequestedMode.sea
    if "road" in text or "truck" in text:
        return RequestedMode.road
    return RequestedMode.unknown


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
