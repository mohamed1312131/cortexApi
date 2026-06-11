from __future__ import annotations

from app.schemas import CaseAction, CaseState


SHIPMENT_TERMS = {
    "ship",
    "shipment",
    "transport",
    "move",
    "cargo",
    "freight",
    "deliver",
    "send",
}

MODE_TERMS = {"air", "sea", "road", "truck", "ocean", "flight"}


def route_message(message: str, state: CaseState | None = None) -> CaseAction:
    text = _normalize(message)
    has_case = bool(state and state.current_shipment_request)

    if not text:
        return CaseAction.unknown

    if _starts_new_case(text):
        return CaseAction.start_new_case if has_case else CaseAction.create_new_case

    if _is_intake_explanation_question(text):
        return CaseAction.answer_intake_question

    if has_case and _is_mode_comparison(text):
        return CaseAction.compare_mode_request

    if has_case and _is_mode_change(text):
        return CaseAction.change_mode

    if has_case and _is_existing_report_question(text):
        return CaseAction.ask_detail_about_existing_report

    if has_case and _is_report_filter(text):
        return CaseAction.filter_existing_report

    if has_case and _looks_like_case_update(text):
        return CaseAction.update_existing_case

    if has_case and state and state.last_missing_questions:
        return CaseAction.clarify_missing_field

    if _looks_like_shipment_request(text):
        return CaseAction.create_new_case

    return CaseAction.unknown


def _normalize(message: str) -> str:
    return " ".join(message.lower().strip().split())


def _starts_new_case(text: str) -> bool:
    starters = (
        "new shipment",
        "new case",
        "another shipment",
        "different shipment",
        "start over",
        "start a new",
    )
    return any(starter in text for starter in starters)


# Self-evident explanation requests. These are explanation-only regardless of
# which intake term they mention, so they must NOT require a domain term.
_STANDALONE_EXPLANATION_PHRASES = (
    "why do you need",
    "why are you asking",
    "why did you ask",
    "why is this needed",
    "what should i answer",
    "what should i put for",
    "how do i know",
    "explain why",
)

_EXPLANATION_TERMS = (
    "what is",
    "what's",
    "explain",
    "meaning of",
    "what does",
    "why do you need",
)

_EXPLANATION_DOMAIN_TERMS = (
    "un number",
    "un38.3",
    "un 38.3",
    "hs code",
    "incoterm",
    "packing group",
    "sds",
    "placi",
    "dangerous goods",
    "state of charge",
    "soc",
    "battery packing",
    "packing configuration",
)


def _is_intake_explanation_question(text: str) -> bool:
    if any(phrase in text for phrase in _STANDALONE_EXPLANATION_PHRASES):
        return True
    return any(term in text for term in _EXPLANATION_TERMS) and any(
        term in text for term in _EXPLANATION_DOMAIN_TERMS
    )


def _is_existing_report_question(text: str) -> bool:
    return text.startswith(("why ", "how ", "what ")) and any(
        term in text
        for term in (
            "recommended",
            "not recommended",
            "blocked",
            "risk",
            "report",
            "reason",
            "road",
            "air",
            "sea",
        )
    )


def _is_report_filter(text: str) -> bool:
    return (
        text.startswith(("show ", "only ", "filter "))
        and any(term in text for term in ("documents", "cost", "risks", "blockers", "road", "air", "sea"))
    )


def _is_mode_comparison(text: str) -> bool:
    return (
        any(phrase in text for phrase in ("what about", "compare", "versus", "vs", "instead"))
        and any(mode in text for mode in MODE_TERMS)
    )


def _is_mode_change(text: str) -> bool:
    return any(phrase in text for phrase in ("use air", "use sea", "use road", "by air", "by sea", "by road"))


def _looks_like_case_update(text: str) -> bool:
    update_markers = (
        "actually",
        "correction",
        "it is",
        "it's",
        "from ",
        "to ",
        "un",
        "kg",
        "cbm",
        "tons",
        "tonnes",
        "deadline",
        "ready",
        "incoterm",
        "dimensions",
    )
    return any(marker in text for marker in update_markers)


def _looks_like_shipment_request(text: str) -> bool:
    return any(term in text for term in SHIPMENT_TERMS) or (
        " from " in f" {text} " and " to " in f" {text} "
    )
