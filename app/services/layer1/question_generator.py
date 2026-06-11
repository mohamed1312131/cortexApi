from __future__ import annotations

from app.schemas import MissingFields, QuestionToUser, ValidatedShipmentRequest
from app.services.layer1.semantic_validator import CARGO_UN_CONFLICT_BLOCKER


def generate_questions(
    request: ValidatedShipmentRequest,
    missing_fields: MissingFields,
    *,
    limit: int = 3,
) -> list[QuestionToUser]:
    questions: list[QuestionToUser] = []
    seen_targets: set[str] = set()
    for field in missing_fields.blocking:
        question = _question_for_field(request, field)
        if question and question.field_target not in seen_targets:
            questions.append(question)
            seen_targets.add(question.field_target)
        if len(questions) >= limit:
            return questions

    for field in missing_fields.high_value:
        question = _question_for_field(request, field)
        if question and question.field_target not in seen_targets:
            questions.append(question)
            seen_targets.add(question.field_target)
        if len(questions) >= limit:
            return questions

    return questions


def build_assistant_message(
    decision_label: str,
    questions: list[QuestionToUser],
) -> str:
    if not questions:
        return decision_label

    lines = [decision_label]
    for index, item in enumerate(questions, start=1):
        lines.append(f"{index}. {item.question}")
    lines.append("If you do not know one of these yet, say so and I will keep it marked as unknown.")
    return "\n".join(lines)


def _question_for_field(
    request: ValidatedShipmentRequest,
    field: str,
) -> QuestionToUser | None:
    if field == CARGO_UN_CONFLICT_BLOCKER:
        cargo = request.core_shipment.cargo_description or "the stated cargo"
        un_number = _conflict_un_number(request) or "the UN number"
        return QuestionToUser(
            question=(
                f"You wrote {cargo} but {un_number} is associated with lithium ion batteries. "
                f"Is the cargo {cargo}, lithium batteries, or did you mean a different UN number?"
            ),
            reason="Cargo description and dangerous-goods classification must agree before Layer 2 checks are reliable.",
            field_target="profiles.dangerous_goods.un_number/core_shipment.cargo_description",
        )

    if field in {
        "valid UN number or dangerous-goods classification",
        "UN number or dangerous-goods classification",
        "UN number or battery type",
    }:
        invalid_un = _invalid_un_token(request)
        if invalid_un:
            return QuestionToUser(
                question=(
                    f"I saw {invalid_un}, but UN numbers must have 4 digits. "
                    "Did you mean UN3480, UN3481, UN3090, or UN3091?"
                ),
                reason="This changes dangerous-goods restrictions for air, sea, and road.",
                field_target="profiles.dangerous_goods.un_number",
            )
        return QuestionToUser(
            question=(
                "Do you know the UN number? For lithium batteries it is usually "
                "UN3480, UN3481, UN3090, or UN3091."
            ),
            reason="This changes dangerous-goods restrictions for air, sea, and road.",
            field_target="profiles.dangerous_goods.un_number",
        )

    if field in {"origin and destination", "origin city", "destination city"}:
        if request.lane.origin_city is None and request.lane.destination_city is None:
            question = "What are the origin and destination cities?"
            target = "lane.origin_city/lane.destination_city"
        elif request.lane.origin_city is None:
            question = "What is the origin city?"
            target = "lane.origin_city"
        else:
            question = "What is the destination city?"
            target = "lane.destination_city"
        return QuestionToUser(
            question=question,
            reason="City-level detail improves port, airport, road, and schedule preparation.",
            field_target=target,
        )

    if field == "weight or quantity":
        return QuestionToUser(
            question="What is the approximate total weight, or at least the quantity?",
            reason="Weight and quantity can change load fit, mode preparation, and cost references.",
            field_target="core_shipment.weight_kg/core_shipment.quantity",
        )

    if field == "valid positive weight or quantity":
        return QuestionToUser(
            question="Please provide a valid positive weight, or at least the quantity.",
            reason="Weight and quantity must be positive before load fit, mode preparation, and cost references are reliable.",
            field_target="core_shipment.weight_kg/core_shipment.quantity",
        )

    if field == "cargo description":
        return QuestionToUser(
            question="What goods are you shipping?",
            reason="Cargo type determines which logistics checks are relevant.",
            field_target="core_shipment.cargo_description",
        )

    if field == "dimensions":
        return QuestionToUser(
            question="What are the cargo dimensions: length, width, and height?",
            reason="Oversized cargo can change vehicle, container, aircraft, permit, and route checks.",
            field_target="core_shipment.dimensions",
        )

    if field == "volume or dimensions":
        return QuestionToUser(
            question="Do you know the volume in CBM or the cargo dimensions?",
            reason="This improves container, vehicle, aircraft fit, and cost references.",
            field_target="core_shipment.volume_cbm/core_shipment.dimensions",
        )

    if field == "ready date or deadline":
        return QuestionToUser(
            question="Is there a ready date or delivery deadline?",
            reason="Timing changes schedule and route-readiness checks.",
            field_target="commercial.ready_date/commercial.deadline",
        )

    if field == "temperature range":
        return QuestionToUser(
            question="What temperature range does the cargo need?",
            reason="Cold-chain cargo needs temperature preparation before mode checks are reliable.",
            field_target="profiles.temperature_controlled.temperature_range",
        )

    if field == "cold-chain packaging type":
        return QuestionToUser(
            question="What cold-chain packaging type will be used?",
            reason="Packaging affects temperature-control readiness across modes.",
            field_target="profiles.temperature_controlled.packaging_type",
        )

    if field == "shelf life":
        return QuestionToUser(
            question="What is the remaining shelf life?",
            reason="Shelf life affects schedule and cold-chain urgency.",
            field_target="profiles.temperature_controlled.shelf_life",
        )

    if field == "SDS availability":
        return QuestionToUser(
            question="Do you have the Safety Data Sheet?",
            reason="Hazardous or chemical cargo needs SDS evidence before reliable transport checks.",
            field_target="profiles.dangerous_goods.sds_available",
        )

    if field == "battery packing configuration":
        return QuestionToUser(
            question="Are the batteries shipped alone, packed with equipment, or contained in equipment?",
            reason="Battery packing configuration changes dangerous-goods preparation.",
            field_target="profiles.lithium_battery.packed_with_equipment",
        )

    if field == "state of charge for air":
        return QuestionToUser(
            question="If air transport is possible, do you know the lithium battery state of charge?",
            reason="State of charge can affect air dangerous-goods preparation.",
            field_target="profiles.lithium_battery.state_of_charge_pct",
        )

    if field == "UN38.3 availability":
        return QuestionToUser(
            question="Do you have UN38.3 test documentation?",
            reason="Lithium battery transport often requires this evidence before carrier review.",
            field_target="profiles.lithium_battery.un38_3_available",
        )

    if field == "single-piece weight":
        return QuestionToUser(
            question="What is the heaviest single-piece weight?",
            reason="Heavy pieces can change equipment, permits, handling, and load fit.",
            field_target="profiles.oversized.single_piece_weight_kg",
        )

    if field == "stackability":
        return QuestionToUser(
            question="Is the cargo stackable?",
            reason="Stackability affects load planning and equipment fit.",
            field_target="profiles.oversized.stackable",
        )

    if field == "lifting points":
        return QuestionToUser(
            question="Are lifting points or lifting instructions available?",
            reason="Large or heavy cargo may need handling validation before transport preparation.",
            field_target="profiles.oversized.lifting_points",
        )

    if field == "animal species":
        return QuestionToUser(
            question="What animal species are being transported?",
            reason="Live-animal requirements depend heavily on species.",
            field_target="profiles.live_animals.species",
        )

    if field == "health documents availability":
        return QuestionToUser(
            question="Are veterinary or health documents available?",
            reason="Live-animal shipments need health evidence before route and document checks are reliable.",
            field_target="profiles.live_animals.health_documents_available",
        )

    if field == "vehicle fuel/battery status":
        return QuestionToUser(
            question="What is the vehicle fuel or battery status?",
            reason="Vehicles can trigger special handling and dangerous-goods preparation.",
            field_target="profiles.vehicle.fuel_status",
        )

    if field == "cargo value":
        return QuestionToUser(
            question="What is the approximate cargo value and currency?",
            reason="High-value cargo can change security, insurance, and handling preparation.",
            field_target="commercial.cargo_value/commercial.currency",
        )

    if field == "cold-chain requirement":
        return QuestionToUser(
            question="Is a controlled cold chain required for the whole move?",
            reason="Pharma cargo may need controlled handling from pickup to delivery.",
            field_target="profiles.pharma.cold_chain_required",
        )

    return None


def _invalid_un_token(request: ValidatedShipmentRequest) -> str | None:
    for key in ("rejected_fields", "validation_warnings"):
        issues = request.inferred_flags.get(key, [])
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if issue.get("field") == "profiles.dangerous_goods.un_number" and issue.get("value"):
                return str(issue["value"])
    return None


def _conflict_un_number(request: ValidatedShipmentRequest) -> str | None:
    conflicts = request.inferred_flags.get("validation_conflicts", [])
    if not isinstance(conflicts, list):
        return None
    for conflict in conflicts:
        if isinstance(conflict, dict) and conflict.get("code") == "cargo_un_conflict":
            value = conflict.get("un_number")
            return str(value) if value else None
    return None
