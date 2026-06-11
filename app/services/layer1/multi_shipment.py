from __future__ import annotations

from app.schemas import MissingFields, QuestionToUser, ValidatedShipmentRequest


MULTIPLE_SHIPMENT_BLOCKER = "single shipment selection"
MULTIPLE_SHIPMENT_ASSISTANT_MESSAGE = (
    "I detected multiple shipments in one message. To avoid merging separate cargo and lanes, "
    "please choose one shipment to continue with or send each shipment separately."
)


def build_multiple_shipment_question() -> QuestionToUser:
    return QuestionToUser(
        question=(
            "Please choose one shipment to continue with, or send each shipment separately."
        ),
        reason="Layer 1 builds one intake request per shipment and should not merge separate cargos or lanes.",
        field_target="shipment.selection",
    )


def build_multiple_shipment_request(case_id: str) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=case_id,
        missing_fields=MissingFields(blocking=[MULTIPLE_SHIPMENT_BLOCKER]),
        questions_to_user=[build_multiple_shipment_question()],
        ready_for_layer_2=False,
        inferred_flags={"multiple_shipments_detected": True},
    )
