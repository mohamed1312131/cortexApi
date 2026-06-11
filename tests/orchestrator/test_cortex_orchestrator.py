from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    CargoFlags,
    CaseAction,
    Completeness,
    CompletenessStatus,
    CoreShipment,
    FactPackage,
    FactPackageRollup,
    FetchPlan,
    FlagState,
    IntakeDecision,
    IntakeIntent,
    IntakeResult,
    Lane,
    ModeSelection,
    QuestionToUser,
    RequestedMode,
    ValidatedShipmentRequest,
)


def test_cortex_message_incomplete_intake_does_not_call_layer2(monkeypatch):
    def fake_layer1(**kwargs):
        return _intake_result(
            conversation_id=kwargs["conversation_id"],
            case_id="case-incomplete",
            ready_for_layer_2=False,
            questions_to_user=[
                QuestionToUser(
                    question="Do you know the UN number?",
                    reason="This changes dangerous-goods restrictions.",
                    field_target="profiles.dangerous_goods.un_number",
                )
            ],
        )

    def fail_layer2(_request):
        raise AssertionError("Layer 2 should not run for incomplete intake")

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fake_layer1,
    )
    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.build_fact_package_for_request",
        fail_layer2,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-incomplete",
                "message": "I need to move 8000 kg lithium batteries from China to France.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ASK_USER"
    assert payload["layer1"]["ready_for_layer_2"] is False
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is False
    assert payload["layer1"]["questions_to_user"]


def test_cortex_message_completed_intake_calls_layer2(monkeypatch):
    layer2_calls = []

    def fake_layer1(**kwargs):
        if "UN3480" not in kwargs["message"]:
            return _intake_result(
                conversation_id=kwargs["conversation_id"],
                case_id="case-completed",
                ready_for_layer_2=False,
                questions_to_user=[
                    QuestionToUser(
                        question="Do you know the UN number?",
                        reason="This changes dangerous-goods restrictions.",
                        field_target="profiles.dangerous_goods.un_number",
                    )
                ],
            )

        return _intake_result(
            conversation_id=kwargs["conversation_id"],
            case_id="case-completed",
            ready_for_layer_2=True,
            intake_json=_shipment_request(case_id="case-completed", complete=True),
        )

    def fake_layer2(request):
        layer2_calls.append(request)
        return _fact_package(request)

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fake_layer1,
    )
    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.build_fact_package_for_request",
        fake_layer2,
    )

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-completed",
                "message": "I need to move 8000 kg lithium batteries from China to France.",
            },
        )
        second = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-completed",
                "message": "It is UN3480, from Shenzhen to Lyon, volume is 20 CBM.",
            },
        )

    assert first.status_code == 200
    assert first.json()["next_action"] == "ASK_USER"
    assert len(layer2_calls) == 1

    payload = second.json()
    assert second.status_code == 200
    assert payload["next_action"] == "SHOW_FACT_PACKAGE"
    assert payload["layer1"]["ready_for_layer_2"] is True
    assert payload["layer2"] is not None
    assert payload["debug"]["layer2_ran"] is True
    assert "layer3" not in payload


def test_intake_message_stays_layer1_only(monkeypatch):
    def fake_layer1(**kwargs):
        return _intake_result(
            conversation_id=kwargs["conversation_id"],
            case_id="case-intake-only",
            ready_for_layer_2=False,
        )

    monkeypatch.setattr("app.api.v1.routes_intake.handle_intake_message", fake_layer1)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/intake/message",
            json={
                "conversation_id": "conv-intake-only",
                "message": "I need to move cargo.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert "intake_json" in payload
    assert "layer2" not in payload
    assert "next_action" not in payload


def test_cortex_message_layer2_failure_returns_structured_error(monkeypatch):
    def fake_layer1(**kwargs):
        return _intake_result(
            conversation_id=kwargs["conversation_id"],
            case_id="case-layer2-error",
            ready_for_layer_2=True,
            intake_json=_shipment_request(case_id="case-layer2-error", complete=True),
        )

    def fail_layer2(_request):
        raise RuntimeError("fact package unavailable")

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fake_layer1,
    )
    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.build_fact_package_for_request",
        fail_layer2,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-layer2-error",
                "message": "It is UN3480, from Shenzhen to Lyon, volume is 20 CBM.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ERROR"
    assert payload["layer1"]["ready_for_layer_2"] is True
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is True
    assert "RuntimeError: fact package unavailable" == payload["debug"]["error"]


def test_cortex_message_layer1_failure_returns_json_503(monkeypatch):
    def fail_layer1(**_kwargs):
        raise RuntimeError("intake provider unavailable")

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fail_layer1,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-layer1-error",
                "message": "I need to move cargo.",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "intake provider unavailable"}


def test_cortex_message_independent_conversations_do_not_share_case_id(monkeypatch):
    def fake_layer1(**kwargs):
        return _intake_result(
            conversation_id=kwargs["conversation_id"],
            case_id=f"case-{kwargs['conversation_id']}",
            ready_for_layer_2=False,
        )

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message",
        fake_layer1,
    )

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-independent-a",
                "message": "I need to move cargo.",
            },
        )
        second = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "conv-independent-b",
                "message": "I need to move cargo.",
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["case_id"] != second.json()["case_id"]
    assert first.json()["layer1"]["case_id"] != second.json()["layer1"]["case_id"]


def _intake_result(
    *,
    conversation_id: str,
    case_id: str,
    ready_for_layer_2: bool,
    intake_json: ValidatedShipmentRequest | None = None,
    questions_to_user: list[QuestionToUser] | None = None,
) -> IntakeResult:
    request = intake_json or _shipment_request(case_id=case_id, complete=ready_for_layer_2)
    questions = questions_to_user or []
    request.ready_for_layer_2 = ready_for_layer_2
    request.questions_to_user = questions
    return IntakeResult(
        conversation_id=conversation_id,
        case_id=case_id,
        case_action=CaseAction.create_new_case,
        intent=IntakeIntent.shipment_readiness,
        decision=(
            IntakeDecision.ready_for_layer_2
            if ready_for_layer_2
            else IntakeDecision.ask_user
        ),
        assistant_message="Layer 1 complete." if ready_for_layer_2 else "Layer 1 needs input.",
        intake_json=request,
        ready_for_layer_2=ready_for_layer_2,
        questions_to_user=questions,
    )


def _shipment_request(
    *,
    case_id: str,
    complete: bool,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=case_id,
        core_shipment=CoreShipment(
            cargo_description="lithium batteries",
            weight_kg=8000,
            volume_cbm=20 if complete else None,
        ),
        lane=Lane(
            origin_raw="Shenzhen" if complete else "China",
            destination_raw="Lyon" if complete else "France",
            origin_country="CN",
            destination_country="FR",
            origin_city="Shenzhen" if complete else None,
            destination_city="Lyon" if complete else None,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.unknown,
            candidate_modes=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
            needs_mode_selection=True,
        ),
        cargo_flags=CargoFlags(dangerous_goods=FlagState.likely),
        active_profiles=["dangerous_goods", "lithium_battery"],
        profiles={
            "dangerous_goods": {"un_number": "UN3480" if complete else None},
            "lithium_battery": {
                "battery_type": None,
                "packed_with_equipment": None,
                "state_of_charge_pct": None,
                "un38_3_available": None,
            },
        },
        ready_for_layer_2=complete,
    )


def _fact_package(request: ValidatedShipmentRequest) -> FactPackage:
    return FactPackage(
        case_id=request.case_id,
        request=request,
        fetch_plan=FetchPlan(case_id=request.case_id),
        completeness=Completeness(status=CompletenessStatus.incomplete_but_usable),
        derived_rollup=FactPackageRollup(),
    )
