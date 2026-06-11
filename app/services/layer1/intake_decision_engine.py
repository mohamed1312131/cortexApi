from __future__ import annotations

from app.schemas import CaseAction, IntakeDecision, IntakeIntent, ValidatedShipmentRequest


def decide_next_step(
    request: ValidatedShipmentRequest | None,
    *,
    action: CaseAction,
    intent: IntakeIntent,
) -> IntakeDecision:
    if action is CaseAction.answer_intake_question or intent is IntakeIntent.ask_explanation:
        return IntakeDecision.answer_user_explanation

    if request is None:
        return IntakeDecision.start_new_case if action is CaseAction.start_new_case else IntakeDecision.ask_user

    if request.missing_fields.blocking:
        return IntakeDecision.ask_user

    if action in {CaseAction.update_existing_case, CaseAction.compare_mode_request, CaseAction.change_mode}:
        return IntakeDecision.update_case_and_rerun

    if request.missing_fields.high_value or request.missing_fields.can_wait:
        return IntakeDecision.ready_for_layer_2_with_unknowns

    return IntakeDecision.ready_for_layer_2


def decision_ready_for_layer_2(decision: IntakeDecision) -> bool:
    return decision in {
        IntakeDecision.ready_for_layer_2,
        IntakeDecision.ready_for_layer_2_with_unknowns,
        IntakeDecision.update_case_and_rerun,
    }
