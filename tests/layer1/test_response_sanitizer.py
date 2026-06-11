from app.schemas import (
    CaseAction,
    IntakeDecision,
    IntakeIntent,
    IntakeResult,
    MissingFields,
    QuestionToUser,
    ValidatedShipmentRequest,
)
from app.services.layer1.response_sanitizer import (
    sanitize_intake_result,
    sanitize_user_facing_text,
)


def test_sanitize_intake_result_user_facing_strings():
    result = _result(
        assistant_message="I stillrecommend confirming the lane.",
        intake_json=ValidatedShipmentRequest(
            case_id="case-sanitize-001",
            missing_fields=MissingFields(blocking=["origincity"]),
            questions_to_user=[
                QuestionToUser(
                    question="What is the battery state ofcharge?",
                    reason="Needed for carrierreview.",
                    field_target="profiles.lithium_battery.state_of_charge_pct",
                )
            ],
        ),
        questions_to_user=[
            QuestionToUser(
                question="How are the batteriesshipped?",
                reason="We need to know if itis packed with equipment.",
                field_target="profiles.lithium_battery.packed_with_equipment",
            )
        ],
    )

    sanitized = sanitize_intake_result(result)

    assert sanitized.assistant_message == "I still recommend confirming the lane."
    assert sanitized.intake_json is not None
    assert sanitized.intake_json.missing_fields.blocking == ["origin city"]
    assert (
        sanitized.intake_json.questions_to_user[0].question
        == "What is the battery state of charge?"
    )
    assert sanitized.intake_json.questions_to_user[0].reason == "Needed for carrier review."
    assert sanitized.questions_to_user[0].question == "How are the batteries shipped?"
    assert sanitized.questions_to_user[0].reason == (
        "We need to know if it is packed with equipment."
    )


def test_sanitize_intake_result_preserves_technical_fields():
    result = _result(
        changed_fields=[
            "lane.origin_city",
            "profiles.lithium_battery.state_of_charge_pct",
        ],
        rerun_scope={
            "changed_fields": [
                "lane.origin_city",
                "profiles.lithium_battery.state_of_charge_pct",
            ],
            "scope": ["air_readiness"],
            "rerun_required": True,
        },
        intake_json=ValidatedShipmentRequest(
            case_id="case-sanitize-002",
            questions_to_user=[
                QuestionToUser(
                    question="Confirm the state ofcharge.",
                    reason="Needed for air,sea,and comparison.",
                    field_target="profiles.lithium_battery.state_of_charge_pct",
                )
            ],
        ),
        questions_to_user=[
            QuestionToUser(
                question="Confirm origincity.",
                reason="Needed for sea,and road checks.",
                field_target="lane.origin_city",
            )
        ],
    )

    sanitized = sanitize_intake_result(result)

    assert sanitized.changed_fields == [
        "lane.origin_city",
        "profiles.lithium_battery.state_of_charge_pct",
    ]
    assert sanitized.rerun_scope["changed_fields"] == [
        "lane.origin_city",
        "profiles.lithium_battery.state_of_charge_pct",
    ]
    assert sanitized.intake_json is not None
    assert (
        sanitized.intake_json.questions_to_user[0].field_target
        == "profiles.lithium_battery.state_of_charge_pct"
    )
    assert sanitized.questions_to_user[0].field_target == "lane.origin_city"


def test_sanitize_user_facing_spacing_regressions():
    result = _result(
        assistant_message="Therequest is ready. Iwill ask: Doyou mean UN3480from Shenzhen?",
    )

    sanitized = sanitize_intake_result(result)

    assert sanitized.assistant_message == (
        "The request is ready. I will ask: Do you mean UN3480 from Shenzhen?"
    )


def test_sanitizer_fixes_fused_to_city_uppercase():
    assert sanitize_user_facing_text("from Shenzhen toLyon") == "from Shenzhen to Lyon"
    assert sanitize_user_facing_text("from Shenzhen toMarseille") == "from Shenzhen to Marseille"


def test_sanitizer_fixes_total_weight():
    assert sanitize_user_facing_text("What is the totalweight?") == "What is the total weight?"


def test_sanitizer_fixes_usually_un_number():
    assert (
        sanitize_user_facing_text("It is usuallyUN3480 for batteries.")
        == "It is usually UN3480 for batteries."
    )


def test_sanitizer_fixes_comma_spacing_between_un_numbers():
    assert (
        sanitize_user_facing_text("Did you mean UN3481,UN3090?")
        == "Did you mean UN3481, UN3090?"
    )


def test_sanitizer_preserves_digit_grouped_numbers():
    assert sanitize_user_facing_text("Weight is 8,000 kg total.") == "Weight is 8,000 kg total."


def test_sanitize_intake_result_cleans_assistant_message():
    result = _result(
        assistant_message="The totalweight is unknown for UN3481,UN3090 toLyon.",
    )

    sanitized = sanitize_intake_result(result)

    assert sanitized.assistant_message == (
        "The total weight is unknown for UN3481, UN3090 to Lyon."
    )


def test_sanitize_intake_result_cleans_intake_json_questions():
    result = _result(
        intake_json=ValidatedShipmentRequest(
            case_id="case-sanitize-003",
            questions_to_user=[
                QuestionToUser(
                    question="What is the totalweight from Shenzhen toLyon?",
                    reason="Needed for UN3481,UN3090 handling.",
                    field_target="core_shipment.weight_kg",
                )
            ],
        ),
    )

    sanitized = sanitize_intake_result(result)

    assert sanitized.intake_json is not None
    assert (
        sanitized.intake_json.questions_to_user[0].question
        == "What is the total weight from Shenzhen to Lyon?"
    )
    assert (
        sanitized.intake_json.questions_to_user[0].reason
        == "Needed for UN3481, UN3090 handling."
    )


def test_sanitize_intake_result_cleans_top_level_questions():
    result = _result(
        questions_to_user=[
            QuestionToUser(
                question="What is the totalweight from Shenzhen toMarseille?",
                reason="Needed for UN3481,UN3090 handling.",
                field_target="core_shipment.weight_kg",
            )
        ],
    )

    sanitized = sanitize_intake_result(result)

    assert (
        sanitized.questions_to_user[0].question
        == "What is the total weight from Shenzhen to Marseille?"
    )
    assert sanitized.questions_to_user[0].reason == "Needed for UN3481, UN3090 handling."


def test_sanitizer_fixes_fused_to_marseille_uppercase():
    assert sanitize_user_facing_text("from Shenzhen toMarseille") == "from Shenzhen to Marseille"


def test_sanitizer_does_not_corrupt_un_numbers():
    for un in ("UN3480", "UN3481", "UN3090", "UN3091"):
        assert sanitize_user_facing_text(f"The number is {un}.") == f"The number is {un}."


def test_sanitizer_does_not_corrupt_country_codes():
    assert sanitize_user_facing_text("Route CN to FR via DE.") == "Route CN to FR via DE."


def test_sanitizer_does_not_corrupt_incoterms():
    for term in ("EXW", "FOB", "CIF", "DAP"):
        assert sanitize_user_facing_text(f"Incoterm {term} applies.") == f"Incoterm {term} applies."


def test_sanitizer_does_not_corrupt_units():
    text = "Weight 8000 kg and 20 CBM with UN38.3."
    assert sanitize_user_facing_text(text) == text


def _result(**overrides) -> IntakeResult:
    values = {
        "case_id": "case-sanitize",
        "case_action": CaseAction.create_new_case,
        "intent": IntakeIntent.shipment_readiness,
        "decision": IntakeDecision.ask_user,
        "assistant_message": "Ready.",
    }
    values.update(overrides)
    return IntakeResult(**values)
