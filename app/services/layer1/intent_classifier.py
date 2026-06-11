from __future__ import annotations

from app.schemas import CaseAction, IntakeIntent


def classify_intent(message: str, action: CaseAction) -> IntakeIntent:
    text = " ".join(message.lower().strip().split())

    if action is CaseAction.answer_intake_question:
        return IntakeIntent.ask_explanation

    if action in {CaseAction.update_existing_case, CaseAction.clarify_missing_field}:
        return IntakeIntent.follow_up_update

    if action is CaseAction.compare_mode_request:
        return IntakeIntent.mode_comparison

    if any(term in text for term in ("best mode", "best option", "recommend", "choose mode")):
        return IntakeIntent.best_mode_selection

    if any(term in text for term in ("document", "documents", "paperwork", "customs")):
        return IntakeIntent.document_check

    if any(term in text for term in ("cost", "price", "quote", "rate", "budget")):
        return IntakeIntent.cost_planning

    if any(term in text for term in ("deadline", "eta", "transit", "time", "urgent", "ready date")):
        return IntakeIntent.timing_planning

    if any(term in text for term in ("risk", "compliance", "blocked", "allowed", "dangerous")):
        return IntakeIntent.risk_check

    if any(term in text for term in ("ship", "shipment", "move", "transport", "cargo", "freight")):
        return IntakeIntent.shipment_readiness

    if " from " in f" {text} " and " to " in f" {text} ":
        return IntakeIntent.shipment_readiness

    return IntakeIntent.unknown
