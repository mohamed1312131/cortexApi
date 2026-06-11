from __future__ import annotations

import re

from app.schemas import IntakeResult, QuestionToUser, ValidatedShipmentRequest


_TEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("stillrecommend", "still recommend"),
    ("ofcharge", "of charge"),
    ("carrierreview", "carrier review"),
    ("batteriesshipped", "batteries shipped"),
    ("origincity", "origin city"),
    ("destinationcity", "destination city"),
    ("totalweight", "total weight"),
    ("numbersmust", "numbers must"),
    ("air,sea,and", "air, sea, and"),
    ("air, sea,and", "air, sea, and"),
    ("air,sea", "air, sea"),
    ("sea,and", "sea, and"),
    ("iwill", "I will"),
    ("doyou", "do you"),
    ("therequest", "the request"),
    ("isready", "is ready"),
    ("adifferent", "a different"),
)


def sanitize_intake_result(result: IntakeResult) -> IntakeResult:
    sanitized = result.model_copy(deep=True)
    sanitized.assistant_message = sanitize_user_facing_text(sanitized.assistant_message)

    if sanitized.intake_json is not None:
        sanitized.intake_json = _sanitize_shipment_request(sanitized.intake_json)

    sanitized.questions_to_user = [
        _sanitize_question(question)
        for question in sanitized.questions_to_user
    ]

    return sanitized


def sanitize_user_facing_text(value: str | None) -> str:
    if not value:
        return ""

    cleaned = " ".join(str(value).split())

    for bad, good in _TEXT_REPLACEMENTS:
        cleaned = _replace_case_aware(cleaned, bad, good)

    cleaned = re.sub(r"\b(UN\d{4})(?=[A-Za-z])", r"\1 ", cleaned, flags=re.IGNORECASE)

    # Add the missing space after a comma when it is fused to a following word,
    # e.g. "UN3481,UN3090" -> "UN3481, UN3090" or "air,sea" -> "air, sea". The
    # lookahead only fires before a letter, so digit-grouped numbers such as
    # "8,000" keep their thousands separator untouched.
    cleaned = re.sub(r",(?=[A-Za-z])", ", ", cleaned)
    cleaned = re.sub(
        r"\bitis\b",
        lambda match: _match_case(match.group(0), "it is"),
        cleaned,
        flags=re.IGNORECASE,
    )

    # Defensive guard for fused preposition + Capitalized word, e.g. "toLyon" ->
    # "to Lyon", "fromShenzhen" -> "from Shenzhen". Only fires before an uppercase
    # letter, so it never splits acronyms, UN numbers, country codes, or units.
    cleaned = re.sub(r"\b(to|from)([A-Z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)

    return cleaned


def _sanitize_shipment_request(
    request: ValidatedShipmentRequest,
) -> ValidatedShipmentRequest:
    request.missing_fields.blocking = [
        sanitize_user_facing_text(value)
        for value in request.missing_fields.blocking
    ]
    request.missing_fields.high_value = [
        sanitize_user_facing_text(value)
        for value in request.missing_fields.high_value
    ]
    request.missing_fields.can_wait = [
        sanitize_user_facing_text(value)
        for value in request.missing_fields.can_wait
    ]
    request.questions_to_user = [
        _sanitize_question(question)
        for question in request.questions_to_user
    ]
    return request


def _sanitize_question(question: QuestionToUser) -> QuestionToUser:
    question.question = sanitize_user_facing_text(question.question)
    question.reason = sanitize_user_facing_text(question.reason)
    return question


def _replace_case_aware(text: str, bad: str, good: str) -> str:
    return re.sub(
        re.escape(bad),
        lambda match: _match_case(match.group(0), good),
        text,
        flags=re.IGNORECASE,
    )


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement
