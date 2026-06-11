from __future__ import annotations

from app.schemas import CaseAction, IntakeDecision, QuestionToUser, RequestedMode, ValidatedShipmentRequest
from app.services.layer1.intake_decision_engine import decide_next_step, decision_ready_for_layer_2
from app.services.layer1.intake_explanations import explain_intake_term
from app.services.layer1.missing_field_prioritizer import prioritize_missing_fields
from app.services.layer1.multi_shipment import (
    MULTIPLE_SHIPMENT_ASSISTANT_MESSAGE,
    build_multiple_shipment_question,
)
from app.services.layer1.question_generator import build_assistant_message, generate_questions
from app.services.layer1.state import IntakeGraphState


def decide_missing_fields(state: IntakeGraphState) -> IntakeGraphState:
    route = state["route"]
    current = state.get("current_request")
    changed_fields = list(state.get("changed_fields", []))

    if state.get("multiple_shipments_detected"):
        if current is not None:
            current.ready_for_layer_2 = False
            if not current.questions_to_user:
                current.questions_to_user = [build_multiple_shipment_question()]

        return {
            **state,
            "current_request": current,
            "decision": IntakeDecision.ask_user,
            "assistant_message": MULTIPLE_SHIPMENT_ASSISTANT_MESSAGE,
            "requires_layer_2_rerun": False,
            "rerun_scope": {},
        }

    if route.case_action is CaseAction.answer_intake_question:
        return {
            **state,
            "decision": IntakeDecision.answer_user_explanation,
            "assistant_message": explain_intake_term(state.get("message", "")),
            "requires_layer_2_rerun": False,
            "rerun_scope": {},
        }

    if route.case_action in {CaseAction.ask_detail_about_existing_report, CaseAction.filter_existing_report}:
        return {
            **state,
            "decision": IntakeDecision.ask_user,
            "assistant_message": (
                "Layer 1 routed this as a report/view follow-up. It did not change shipment facts "
                "and did not request a Layer 2 rerun."
            ),
            "requires_layer_2_rerun": False,
            "rerun_scope": {},
        }

    if route.case_action is CaseAction.unknown or current is None:
        return {
            **state,
            "decision": IntakeDecision.ask_user,
            "assistant_message": (
                "Tell me what you need to ship, from where, to where, and any weight or quantity you know."
            ),
            "requires_layer_2_rerun": False,
            "rerun_scope": {},
        }

    current.missing_fields = prioritize_missing_fields(current)
    current.questions_to_user = generate_questions(current, current.missing_fields)

    if _has_validation_conflicts(current):
        _CONFLICT_FIELD = "cargo / UN number conflict clarification"
        if _CONFLICT_FIELD not in current.missing_fields.blocking:
            current.missing_fields.blocking.append(_CONFLICT_FIELD)
        current.questions_to_user = generate_questions(current, current.missing_fields)

    decision = decide_next_step(current, action=route.case_action, intent=route.intent)
    if route.case_action is CaseAction.compare_mode_request and not changed_fields:
        decision = (
            IntakeDecision.ask_user
            if current.missing_fields.blocking
            else IntakeDecision.ready_for_layer_2_with_unknowns
        )
    current.ready_for_layer_2 = decision_ready_for_layer_2(decision)
    current.intake_quality_score = _quality_score(current)

    rerun_scope = (
        _rerun_scope(changed_fields)
        if route.case_action in {CaseAction.update_existing_case, CaseAction.compare_mode_request, CaseAction.change_mode}
        else {}
    )

    return {
        **state,
        "current_request": current,
        "decision": decision,
        "assistant_message": _assistant_message_for_action(
            decision,
            route.case_action,
            current.questions_to_user,
            changed_fields,
            state.get("message", ""),
        ),
        "requires_layer_2_rerun": _requires_rerun(route.case_action, changed_fields, rerun_scope),
        "rerun_scope": rerun_scope,
    }


def _assistant_message_for_decision(
    decision: IntakeDecision,
    questions: list[QuestionToUser],
) -> str:
    if decision is IntakeDecision.ask_user:
        return build_assistant_message("To avoid a wrong readiness report, I need:", questions)
    if decision is IntakeDecision.update_case_and_rerun:
        return "I updated the case and marked the affected Layer 2 scope for rerun."
    if decision is IntakeDecision.ready_for_layer_2_with_unknowns:
        return "I have enough structured intake for Layer 2. Remaining non-blocking details stay marked as unknown."
    if decision is IntakeDecision.ready_for_layer_2:
        return "I have a complete enough structured intake for Layer 2."
    if decision is IntakeDecision.start_new_case:
        return build_assistant_message("I started a new shipment case. To continue, I need:", questions)
    return "I updated the Layer 1 intake state."


def _assistant_message_for_action(
    decision: IntakeDecision,
    action: CaseAction,
    questions: list[QuestionToUser],
    changed_fields: list[str],
    message: str,
) -> str:
    if (
        action is CaseAction.compare_mode_request
        and not changed_fields
        and decision is not IntakeDecision.ask_user
    ):
        mode = _mode_from_text(message)
        if mode is not RequestedMode.unknown:
            return (
                f"{mode.value.title()} is already included in the current candidate modes, "
                "so no Layer 2 request change was needed."
            )
    return _assistant_message_for_decision(decision, questions)


def _has_validation_conflicts(request: ValidatedShipmentRequest) -> bool:
    conflicts = request.inferred_flags.get("validation_conflicts", [])
    return isinstance(conflicts, list) and len(conflicts) > 0


def _quality_score(request: ValidatedShipmentRequest) -> float:
    score = 1.0
    score -= min(len(request.missing_fields.blocking) * 0.18, 0.72)
    score -= min(len(request.missing_fields.high_value) * 0.05, 0.2)
    score -= min(len(request.missing_fields.can_wait) * 0.02, 0.08)
    return round(max(score, 0.0), 2)


def _rerun_scope(changed_fields: list[str]) -> dict:
    if not changed_fields:
        return {}
    return {
        "changed_fields": _dedupe(changed_fields),
        "rerun_required": True,
        "scope": _coarse_rerun_scope(changed_fields),
    }


def _requires_rerun(
    action: CaseAction,
    changed_fields: list[str],
    rerun_scope: dict,
) -> bool:
    if action not in {CaseAction.update_existing_case, CaseAction.compare_mode_request, CaseAction.change_mode}:
        return False
    return bool(rerun_scope.get("rerun_required") or changed_fields)


def _coarse_rerun_scope(changed_fields: list[str]) -> list[str]:
    scope: list[str] = []
    for field in changed_fields:
        if field.startswith("lane."):
            scope.extend(["node_resolution", "mode_specific_readiness"])
        elif field.startswith("mode."):
            scope.extend(["mode_selection", "mode_specific_readiness"])
        elif field.startswith("cargo_flags.") or field.startswith("profiles."):
            scope.extend(["dg_checks", "mode_specific_readiness"])
        elif field.startswith("core_shipment."):
            scope.extend(["container_fit", "chargeable_weight", "vehicle_fit", "cost_planning", "mode_specific_readiness"])
        elif field.startswith("commercial."):
            scope.extend(["documents", "cost_planning", "schedule_readiness"])
        elif field.startswith("user_goal."):
            scope.append("mode_selection")
    return _dedupe(scope)


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
