from __future__ import annotations
from typing import Any

from app.schemas import CaseAction, CaseStatus, IntakeDecision, IntakeResult
from app.services.layer1.case_state_manager import InMemoryCaseStateStore, RedisCaseStateStore
from app.services.layer1.response_sanitizer import (
    sanitize_intake_result,
    sanitize_user_facing_text,
)
from app.services.layer1.state import IntakeGraphState


def persist_intake_state(
    state: IntakeGraphState,
    store: InMemoryCaseStateStore | RedisCaseStateStore,
) -> IntakeGraphState:
    case_state = state["case_state"]
    previous = state.get("previous_request")
    current = state.get("current_request")
    decision = state["decision"]

    if state.get("user_id") and not case_state.user_id:
        case_state.user_id = state.get("user_id")
    if state.get("company_id") and not case_state.company_id:
        case_state.company_id = state.get("company_id")

    changed_fields = _dedupe(
        list(state.get("changed_fields", []))
        + _detect_request_changed_fields(previous, current)
    )

    if current is not None:
        if changed_fields or case_state.current_shipment_request is None:
            case_state.shipment_request_version += 1

        case_state.current_shipment_request = current
        case_state.active_profiles = list(current.active_profiles)
        case_state.last_missing_questions = [
            question.field_target for question in current.questions_to_user
        ]

    case_state.status = _status_for_decision(
        decision,
        state["route"].case_action,
        case_state.status,
    )

    rerun_scope = _build_layer1_rerun_scope(
        changed_fields=changed_fields,
        existing_scope=state.get("rerun_scope", {}),
        decision=decision,
    )

    assistant_message = _build_user_facing_assistant_message(
        current=current,
        existing_message=state.get("assistant_message", ""),
        decision=decision,
    )

    current = _clean_user_facing_strings(current)
    assistant_message = _clean_user_facing_text(assistant_message)

    store.save(case_state)

    result = IntakeResult(
        conversation_id=case_state.conversation_id,
        case_id=case_state.case_id,
        case_action=state["route"].case_action,
        intent=state["route"].intent,
        decision=decision,
        assistant_message=assistant_message,
        intake_json=current,
        ready_for_layer_2=bool(current and current.ready_for_layer_2),
        requires_layer_2_rerun=rerun_scope.get("rerun_required", False),
        changed_fields=changed_fields,
        rerun_scope=rerun_scope,
        questions_to_user=current.questions_to_user if current else [],
    )

    result = _clean_intake_result(result)

    return {
        **state,
        "case_state": case_state,
        "changed_fields": changed_fields,
        "rerun_scope": rerun_scope,
        "requires_layer_2_rerun": rerun_scope.get("rerun_required", False),
        "assistant_message": assistant_message,
        "result": result,
    }


def _detect_request_changed_fields(previous: Any, current: Any) -> list[str]:
    if previous is None or current is None:
        return []

    changed: list[str] = []

    comparisons = {
        "core_shipment.cargo_description": (
            previous.core_shipment.cargo_description,
            current.core_shipment.cargo_description,
        ),
        "core_shipment.weight_kg": (
            previous.core_shipment.weight_kg,
            current.core_shipment.weight_kg,
        ),
        "core_shipment.volume_cbm": (
            previous.core_shipment.volume_cbm,
            current.core_shipment.volume_cbm,
        ),
        "core_shipment.dimensions": (
            previous.core_shipment.dimensions,
            current.core_shipment.dimensions,
        ),
        "lane.origin_raw": (
            previous.lane.origin_raw,
            current.lane.origin_raw,
        ),
        "lane.destination_raw": (
            previous.lane.destination_raw,
            current.lane.destination_raw,
        ),
        "lane.origin_country": (
            previous.lane.origin_country,
            current.lane.origin_country,
        ),
        "lane.destination_country": (
            previous.lane.destination_country,
            current.lane.destination_country,
        ),
        "lane.origin_city": (
            previous.lane.origin_city,
            current.lane.origin_city,
        ),
        "lane.destination_city": (
            previous.lane.destination_city,
            current.lane.destination_city,
        ),
        "mode.requested_mode": (
            previous.mode.requested_mode,
            current.mode.requested_mode,
        ),
        "cargo_flags.dangerous_goods": (
            previous.cargo_flags.dangerous_goods,
            current.cargo_flags.dangerous_goods,
        ),
        "cargo_flags.temperature_controlled": (
            previous.cargo_flags.temperature_controlled,
            current.cargo_flags.temperature_controlled,
        ),
        "cargo_flags.oversized": (
            previous.cargo_flags.oversized,
            current.cargo_flags.oversized,
        ),
        "commercial.incoterm": (
            previous.commercial.incoterm,
            current.commercial.incoterm,
        ),
        "commercial.ready_date": (
            previous.commercial.ready_date,
            current.commercial.ready_date,
        ),
        "commercial.deadline": (
            previous.commercial.deadline,
            current.commercial.deadline,
        ),
    }

    for field_path, (before, after) in comparisons.items():
        if before != after:
            changed.append(field_path)

    previous_dg = previous.profiles.get("dangerous_goods", {})
    current_dg = current.profiles.get("dangerous_goods", {})
    if previous_dg.get("un_number") != current_dg.get("un_number"):
        changed.append("profiles.dangerous_goods.un_number")

    previous_lithium = previous.profiles.get("lithium_battery", {})
    current_lithium = current.profiles.get("lithium_battery", {})

    for field_name in [
        "battery_type",
        "packed_with_equipment",
        "state_of_charge_pct",
        "un38_3_available",
    ]:
        if previous_lithium.get(field_name) != current_lithium.get(field_name):
            changed.append(f"profiles.lithium_battery.{field_name}")

    if previous.active_profiles != current.active_profiles:
        changed.append("active_profiles")

    return _dedupe(changed)


def _build_layer1_rerun_scope(
    changed_fields: list[str],
    existing_scope: dict[str, Any],
    decision: IntakeDecision,
) -> dict[str, Any]:
    scope = set(existing_scope.get("scope", []))

    if not changed_fields:
        return {
            **existing_scope,
            "changed_fields": [],
            "rerun_required": False,
            "scope": sorted(scope),
        }

    for field in changed_fields:
        if field.startswith("lane."):
            scope.update(
                {
                    "node_resolution",
                    "mode_specific_readiness",
                    "documents_border_rules",
                    "confidence_completeness",
                }
            )

        if field.startswith("profiles.dangerous_goods") or field == "cargo_flags.dangerous_goods":
            scope.update(
                {
                    "dg_checks",
                    "mode_specific_readiness",
                    "documents_border_rules",
                    "confidence_completeness",
                }
            )

        if field.startswith("profiles.lithium_battery"):
            scope.update(
                {
                    "dg_checks",
                    "air_readiness",
                    "documents_border_rules",
                    "confidence_completeness",
                }
            )

        if field in {
            "core_shipment.weight_kg",
            "core_shipment.volume_cbm",
            "core_shipment.dimensions",
        }:
            scope.update(
                {
                    "container_fit",
                    "chargeable_weight",
                    "vehicle_fit",
                    "cost_planning",
                    "confidence_completeness",
                }
            )

        if field.startswith("commercial."):
            scope.update(
                {
                    "cost_planning",
                    "timing_planning",
                    "confidence_completeness",
                }
            )

        if field.startswith("mode."):
            scope.update(
                {
                    "mode_specific_readiness",
                    "confidence_completeness",
                }
            )

    rerun_required = decision in {
        IntakeDecision.ready_for_layer_2,
        IntakeDecision.ready_for_layer_2_with_unknowns,
        IntakeDecision.update_case_and_rerun,
    }

    return {
        "changed_fields": changed_fields,
        "rerun_required": bool(rerun_required and changed_fields),
        "scope": sorted(scope),
    }


def _build_user_facing_assistant_message(
    current: Any,
    existing_message: str,
    decision: IntakeDecision,
) -> str:
    if current is None:
        return existing_message

    if decision in {
        IntakeDecision.ready_for_layer_2,
        IntakeDecision.ready_for_layer_2_with_unknowns,
        IntakeDecision.update_case_and_rerun,
    }:
        lane = current.lane
        shipment = current.core_shipment
        dg = current.profiles.get("dangerous_goods", {})
        un_number = dg.get("un_number")

        parts: list[str] = []

        if shipment.cargo_description:
            parts.append(str(shipment.cargo_description))

        if shipment.weight_kg:
            parts.append(f"{shipment.weight_kg:g} kg")

        if shipment.volume_cbm:
            parts.append(f"{shipment.volume_cbm:g} CBM")

        if un_number:
            parts.append(str(un_number))

        lane_text = ""
        if lane.origin_city and lane.destination_city:
            lane_text = f" from {lane.origin_city} to {lane.destination_city}"
        elif lane.origin_raw and lane.destination_raw:
            lane_text = f" from {lane.origin_raw} to {lane.destination_raw}"

        shipment_text = ", ".join(parts) if parts else "the shipment"

        if current.questions_to_user:
            return (
                f"I updated the shipment: {shipment_text}{lane_text}. "
                "The request is ready for the data-checking step. "
                "I still recommend confirming the remaining high-value details below."
            )

        return (
            f"I updated the shipment: {shipment_text}{lane_text}. "
            "The request is ready for the data-checking step."
        )

    return existing_message


def _status_for_decision(
    decision: IntakeDecision,
    action: CaseAction,
    current_status: CaseStatus,
) -> CaseStatus:
    if action in {
        CaseAction.answer_intake_question,
        CaseAction.ask_detail_about_existing_report,
        CaseAction.filter_existing_report,
    }:
        return current_status

    if decision is IntakeDecision.ask_user:
        return CaseStatus.waiting_for_user_clarification

    if decision in {
        IntakeDecision.ready_for_layer_2,
        IntakeDecision.ready_for_layer_2_with_unknowns,
        IntakeDecision.update_case_and_rerun,
    }:
        return CaseStatus.ready_for_layer_2

    return CaseStatus.intake_in_progress


def _clean_intake_result(result: IntakeResult) -> IntakeResult:
    return sanitize_intake_result(result)


def _clean_shipment_request(request):
    request.missing_fields.blocking = [
        _clean_user_facing_text(value)
        for value in request.missing_fields.blocking
    ]
    request.missing_fields.high_value = [
        _clean_user_facing_text(value)
        for value in request.missing_fields.high_value
    ]
    request.missing_fields.can_wait = [
        _clean_user_facing_text(value)
        for value in request.missing_fields.can_wait
    ]

    request.questions_to_user = [
        _clean_question_object(question)
        for question in request.questions_to_user
    ]

    return request


def _clean_question_object(question):
    question.question = _clean_user_facing_text(question.question)
    question.reason = _clean_user_facing_text(question.reason)
    return question


def _clean_user_facing_strings(current):
    if current is None:
        return current

    current = _clean_shipment_request(current)
    return current


def _clean_user_facing_text(value: str | None) -> str:
    return sanitize_user_facing_text(value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)

    return result
