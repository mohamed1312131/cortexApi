"""Boundary-sanitization tests for the embedded layer1 object.

These verify that /api/v1/cortex/message applies the same user-facing
sanitization to its embedded ``layer1`` field as /api/v1/intake/message applies
to its top-level response. Layer 1 is replaced with a deterministic fake that
returns deliberately dirty user-facing strings; only the API boundary should be
responsible for cleaning them here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    CaseAction,
    IntakeDecision,
    IntakeIntent,
    IntakeResult,
    MissingFields,
    QuestionToUser,
    ValidatedShipmentRequest,
)


_DIRTY_ASSISTANT = (
    "I saw UN348, but UN numbersmust have 4 digits. "
    "Did you mean UN3481,UN3090 from Shenzhen toLyon?"
)


def _dirty_intake_result(*, conversation_id: str, case_id: str) -> IntakeResult:
    return IntakeResult(
        conversation_id=conversation_id,
        case_id=case_id,
        case_action=CaseAction.create_new_case,
        intent=IntakeIntent.shipment_readiness,
        decision=IntakeDecision.ask_user,
        assistant_message=_DIRTY_ASSISTANT,
        ready_for_layer_2=False,
        intake_json=ValidatedShipmentRequest(
            case_id=case_id,
            missing_fields=MissingFields(blocking=["origincity"]),
            questions_to_user=[
                QuestionToUser(
                    question="Is the totalweight known from Shenzhen toLyon?",
                    reason="UN numbersmust match for UN3481,UN3090.",
                    field_target="core_shipment.weight_kg",
                )
            ],
        ),
        questions_to_user=[
            QuestionToUser(
                question="Did you mean UN3481,UN3090 from Shenzhen toLyon?",
                reason="UN numbersmust be 4 digits.",
                field_target="profiles.dangerous_goods.un_number",
            )
        ],
    )


def _patch_layer1(monkeypatch) -> None:
    def fake_layer1(**kwargs):
        return _dirty_intake_result(
            conversation_id=kwargs.get("conversation_id"),
            case_id="case-sanitize-cortex",
        )

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fake_layer1,
    )


def _post_cortex(conversation_id: str):
    with TestClient(app) as client:
        return client.post(
            "/api/v1/cortex/message",
            json={"conversation_id": conversation_id, "message": "anything"},
        )


# 1. Embedded layer1.assistant_message is sanitized.
def test_cortex_layer1_assistant_message_sanitized(monkeypatch):
    _patch_layer1(monkeypatch)
    payload = _post_cortex("san-1").json()

    message = payload["layer1"]["assistant_message"]
    assert "numbersmust" not in message
    assert "toLyon" not in message
    assert "UN3481,UN3090" not in message
    assert "numbers must" in message
    assert "to Lyon" in message
    assert "UN3481, UN3090" in message
    # Machine fields preserved.
    assert payload["layer1"]["case_id"] == "case-sanitize-cortex"


# 2. Embedded layer1.intake_json.questions_to_user is sanitized.
def test_cortex_layer1_intake_json_questions_sanitized(monkeypatch):
    _patch_layer1(monkeypatch)
    payload = _post_cortex("san-2").json()

    question = payload["layer1"]["intake_json"]["questions_to_user"][0]
    assert question["question"] == "Is the total weight known from Shenzhen to Lyon?"
    assert question["reason"] == "UN numbers must match for UN3481, UN3090."
    # field_target (machine field) untouched.
    assert question["field_target"] == "core_shipment.weight_kg"
    # missing_fields labels sanitized.
    assert payload["layer1"]["intake_json"]["missing_fields"]["blocking"] == ["origin city"]


# 3. Embedded top-level layer1.questions_to_user is sanitized.
def test_cortex_layer1_top_level_questions_sanitized(monkeypatch):
    _patch_layer1(monkeypatch)
    payload = _post_cortex("san-3").json()

    question = payload["layer1"]["questions_to_user"][0]
    assert question["question"] == "Did you mean UN3481, UN3090 from Shenzhen to Lyon?"
    assert question["reason"] == "UN numbers must be 4 digits."
    assert question["field_target"] == "profiles.dangerous_goods.un_number"


# 4. /intake/message still sanitizes the same fields.
def test_intake_message_still_sanitizes(monkeypatch):
    def fake_layer1(**kwargs):
        return _dirty_intake_result(
            conversation_id=kwargs.get("conversation_id"),
            case_id="case-sanitize-intake",
        )

    monkeypatch.setattr(
        "app.api.v1.routes_intake.handle_intake_message",
        fake_layer1,
    )

    with TestClient(app) as client:
        payload = client.post(
            "/api/v1/intake/message",
            json={"conversation_id": "san-4", "message": "anything"},
        ).json()

    assert "numbersmust" not in payload["assistant_message"]
    assert "to Lyon" in payload["assistant_message"]
    assert "UN3481, UN3090" in payload["assistant_message"]
    assert payload["questions_to_user"][0]["question"] == (
        "Did you mean UN3481, UN3090 from Shenzhen to Lyon?"
    )
    assert payload["intake_json"]["missing_fields"]["blocking"] == ["origin city"]


# 5. Sanitizer does not corrupt machine-like tokens at the endpoint boundary.
def test_cortex_layer1_does_not_corrupt_machine_tokens(monkeypatch):
    clean = "Use UN3480 and UN38.3, 20 CBM, route CN to FR, terms EXW or FOB."

    def fake_layer1(**kwargs):
        result = _dirty_intake_result(
            conversation_id=kwargs.get("conversation_id"),
            case_id="case-sanitize-machine",
        )
        result.assistant_message = clean
        return result

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fake_layer1,
    )

    payload = _post_cortex("san-5").json()
    assert payload["layer1"]["assistant_message"] == clean
