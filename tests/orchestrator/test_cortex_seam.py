"""Layer 1 (agent) -> Layer 2 seam tests for /api/v1/cortex/message.

Layer 1 is now a single intake agent turn plus mechanical plumbing. The agent
call (`run_intake_agent`) is the only non-deterministic part, so it is stubbed
with scripted `AgentTurn`s. Everything downstream — case persistence, diffing,
rerun scope, the orchestrator's defensive `_is_safe_for_layer_2` gate, and the
real Layer 2 fact builder — runs as genuine production code.

Layer 2 (`build_fact_package_for_request`) is wrapped by a spy that records the
argument and either refuses to run (unsafe/incomplete intake) or calls through
to the real deterministic fact builder (ready intake).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    CaseAction,
    CargoFlags,
    CoreShipment,
    FlagState,
    IntakeDecision,
    IntakeIntent,
    Lane,
    MissingFields,
    ModeSelection,
    QuestionToUser,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer1.case_state_manager import case_state_store
from app.services.layer1.intake_agent import AgentTurn
import app.services.orchestrator.cortex_orchestrator as orchestrator
from app.services.layer2.service import (
    build_fact_package_for_request as real_build_fact_package,
)


# --------------------------------------------------------------------------- #
# Scripted agent turns (the only stubbed piece; everything else is real).
# --------------------------------------------------------------------------- #
def _ready_textile_turn(case_id: str) -> AgentTurn:
    return AgentTurn(
        case_action=CaseAction.create_new_case,
        intent=IntakeIntent.shipment_readiness,
        decision=IntakeDecision.ready_for_layer_2_with_unknowns,
        assistant_message="Textiles from Milan to Paris by road — moving to the data check.",
        intake=ValidatedShipmentRequest(
            case_id=case_id,
            core_shipment=CoreShipment(cargo_description="textiles", weight_kg=500),
            lane=Lane(
                origin_raw="Milan",
                destination_raw="Paris",
                origin_city="Milan",
                destination_city="Paris",
                origin_country="IT",
                destination_country="FR",
            ),
            mode=ModeSelection(
                requested_mode=RequestedMode.road,
                candidate_modes=[RequestedMode.road],
                needs_mode_selection=False,
            ),
            active_profiles=["general_cargo"],
            profiles={"general_cargo": {}},
            facts_from_user={"cargo_description": "textiles", "weight_kg": 500},
            missing_fields=MissingFields(can_wait=["incoterm"]),
            ready_for_layer_2=True,
            field_confidence={"cargo_description": 0.9, "weight_kg": 0.9},
            intake_quality_score=0.96,
        ),
    )


def _ask_user_lithium_turn(case_id: str) -> AgentTurn:
    return AgentTurn(
        case_action=CaseAction.create_new_case,
        intent=IntakeIntent.shipment_readiness,
        decision=IntakeDecision.ask_user,
        assistant_message="I need the weight, the cities, and the UN number.",
        intake=ValidatedShipmentRequest(
            case_id=case_id,
            core_shipment=CoreShipment(cargo_description="lithium-ion batteries"),
            lane=Lane(destination_raw="Germany", destination_country="DE"),
            cargo_flags=CargoFlags(dangerous_goods=FlagState.likely),
            active_profiles=["dangerous_goods", "lithium_battery"],
            profiles={
                "dangerous_goods": {"un_number": None},
                "lithium_battery": {
                    "battery_type": None,
                    "packed_with_equipment": None,
                    "state_of_charge_pct": None,
                    "un38_3_available": None,
                },
            },
            missing_fields=MissingFields(
                blocking=["weight or quantity", "origin and destination", "valid UN number or dangerous-goods classification"],
            ),
            questions_to_user=[
                QuestionToUser(
                    question="What is the total weight?",
                    reason="Weight changes preparation.",
                    field_target="core_shipment.weight_kg",
                )
            ],
            ready_for_layer_2=False,
            intake_quality_score=0.3,
        ),
    )


def _ready_lithium_update_turn(case_id: str) -> AgentTurn:
    return AgentTurn(
        case_action=CaseAction.update_existing_case,
        intent=IntakeIntent.follow_up_update,
        decision=IntakeDecision.update_case_and_rerun,
        assistant_message="Updated: UN3480, 8000 kg, Shenzhen to Frankfurt by air.",
        intake=ValidatedShipmentRequest(
            case_id=case_id,
            core_shipment=CoreShipment(cargo_description="lithium-ion batteries", weight_kg=8000),
            lane=Lane(
                origin_raw="Shenzhen",
                destination_raw="Frankfurt",
                origin_city="Shenzhen",
                destination_city="Frankfurt",
                origin_country="CN",
                destination_country="DE",
            ),
            mode=ModeSelection(
                requested_mode=RequestedMode.air,
                candidate_modes=[RequestedMode.air],
                needs_mode_selection=False,
            ),
            cargo_flags=CargoFlags(dangerous_goods=FlagState.yes),
            active_profiles=["dangerous_goods", "lithium_battery"],
            profiles={
                "dangerous_goods": {"un_number": "UN3480"},
                "lithium_battery": {
                    "battery_type": None,
                    "packed_with_equipment": None,
                    "state_of_charge_pct": None,
                    "un38_3_available": None,
                },
            },
            facts_from_user={"un_number": "UN3480", "weight_kg": 8000},
            missing_fields=MissingFields(high_value=["volume or dimensions"]),
            ready_for_layer_2=True,
            field_confidence={"un_number": 0.95, "weight_kg": 0.9},
            intake_quality_score=0.9,
        ),
    )


def _multi_shipment_turn(case_id: str) -> AgentTurn:
    return AgentTurn(
        case_action=CaseAction.unknown,
        intent=IntakeIntent.shipment_readiness,
        decision=IntakeDecision.ask_user,
        assistant_message=(
            "I detected multiple shipments in one message. Please choose one shipment "
            "to continue with or send each shipment separately."
        ),
        intake=ValidatedShipmentRequest(
            case_id=case_id,
            missing_fields=MissingFields(blocking=["single shipment selection"]),
            questions_to_user=[
                QuestionToUser(
                    question="Please choose one shipment to continue with.",
                    reason="One intake request per shipment.",
                    field_target="shipment.selection",
                )
            ],
            inferred_flags={"multiple_shipments_detected": True},
            ready_for_layer_2=False,
        ),
    )


def _inconsistent_ready_turn(case_id: str) -> AgentTurn:
    """Adversarial: the agent claims ready while blocking gaps remain."""
    turn = _ready_textile_turn(case_id)
    turn.intake.missing_fields = MissingFields(blocking=["weight or quantity"])
    turn.intake.ready_for_layer_2 = True
    return turn


def _scripted_agent(message: str, *, case_id: str, previous_request=None, conversation_summary=None, model=None) -> AgentTurn:
    text = message.lower()
    if "two shipments" in text:
        return _multi_shipment_turn(case_id)
    if "claims-ready-but-blocked" in text:
        return _inconsistent_ready_turn(case_id)
    if "un3480" in text:
        return _ready_lithium_update_turn(case_id)
    if "lithium" in text:
        return _ask_user_lithium_turn(case_id)
    if "textile" in text:
        return _ready_textile_turn(case_id)
    return _ask_user_lithium_turn(case_id)


class _Layer2Spy:
    """Records Layer 2 calls. Refuses to run unless call_through is enabled."""

    def __init__(self, *, call_through: bool) -> None:
        self.calls: list[ValidatedShipmentRequest] = []
        self.call_through = call_through

    def __call__(self, request: ValidatedShipmentRequest):
        self.calls.append(request)
        if self.call_through:
            return real_build_fact_package(request)
        raise AssertionError("Layer 2 must not be called for unsafe/incomplete intake")


@pytest.fixture(autouse=True)
def _clear_case_state():
    def _reset() -> None:
        store = getattr(case_state_store, "_fallback", case_state_store)
        if hasattr(store, "_cases"):
            store._cases.clear()
        if hasattr(store, "_conversation_active_case"):
            store._conversation_active_case.clear()

    _reset()
    yield
    _reset()


@pytest.fixture
def scripted_agent(monkeypatch):
    """Stub only the agent turn; all Layer 1 plumbing runs for real."""
    monkeypatch.setattr("app.services.layer1.graph.run_intake_agent", _scripted_agent)


def _install_layer2_spy(monkeypatch, *, call_through: bool) -> _Layer2Spy:
    spy = _Layer2Spy(call_through=call_through)
    monkeypatch.setattr(orchestrator, "build_fact_package_for_request", spy)
    return spy


# --------------------------------------------------------------------------- #
# A. /api/v1/intake/message is Layer 1 only and never reaches Layer 2.
# --------------------------------------------------------------------------- #
def test_A_intake_message_never_calls_layer2(monkeypatch, scripted_agent):
    spy = _install_layer2_spy(monkeypatch, call_through=False)
    monkeypatch.setattr(
        "app.services.layer2.service.build_fact_package_for_request", spy
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/intake/message",
            json={
                "conversation_id": "seam-A",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert "intake_json" in payload
    assert "next_action" not in payload
    assert "layer2" not in payload
    assert spy.calls == []


# --------------------------------------------------------------------------- #
# B. Agent says ask_user (blocking gaps) -> no Layer 2, ASK_USER.
# --------------------------------------------------------------------------- #
def test_B_ask_user_turn_does_not_call_layer2(monkeypatch, scripted_agent):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-B",
                "message": "I need to ship lithium batteries to Germany.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["layer2"] is None
    assert payload["next_action"] != "SHOW_FACT_PACKAGE"
    assert payload["layer1"]["decision"] == "ask_user"
    assert payload["layer1"]["questions_to_user"]
    assert spy.calls == []


# --------------------------------------------------------------------------- #
# C. Agent says ready -> Layer 2 runs and returns a FactPackage.
# --------------------------------------------------------------------------- #
def test_C_ready_turn_calls_layer2(monkeypatch, scripted_agent):
    spy = _install_layer2_spy(monkeypatch, call_through=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-C",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert len(spy.calls) == 1
    assert payload["next_action"] == "SHOW_FACT_PACKAGE"
    assert payload["layer2"] is not None
    assert payload["layer2"]["case_id"] == payload["case_id"]
    assert spy.calls[0].lane.origin_country == "IT"


# --------------------------------------------------------------------------- #
# D. Multi-shipment turn -> blocked at Layer 1, no Layer 2.
# --------------------------------------------------------------------------- #
def test_D_multi_shipment_does_not_call_layer2(monkeypatch, scripted_agent):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-D",
                "message": "I have two shipments: batteries to Lyon and textiles to Paris.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["layer2"] is None
    assert payload["layer1"]["decision"] == "ask_user"
    blocking = payload["layer1"]["intake_json"]["missing_fields"]["blocking"]
    assert "single shipment selection" in blocking
    assert spy.calls == []


# --------------------------------------------------------------------------- #
# E. Two turns: ask_user then ready update -> Layer 2 runs once, case persists.
# --------------------------------------------------------------------------- #
def test_E_two_turn_flow_persists_case_and_calls_layer2(monkeypatch, scripted_agent):
    spy = _install_layer2_spy(monkeypatch, call_through=True)

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-E",
                "message": "I need to ship lithium batteries to Germany.",
            },
        )
        second = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-E",
                "message": "It's UN3480, 8000 kg, Shenzhen to Frankfurt by air.",
            },
        )

    first_payload = first.json()
    second_payload = second.json()

    assert first_payload["layer2"] is None
    assert len(spy.calls) == 1

    assert second_payload["case_id"] == first_payload["case_id"]
    assert second_payload["next_action"] == "SHOW_FACT_PACKAGE"
    assert second_payload["layer2"] is not None
    assert second_payload["layer1"]["requires_layer_2_rerun"] is True
    assert "core_shipment.weight_kg" in second_payload["layer1"]["changed_fields"]
    assert "profiles.dangerous_goods.un_number" in second_payload["layer1"]["changed_fields"]


# --------------------------------------------------------------------------- #
# F. Adversarial agent output (ready while blocking remains) -> gate refuses.
# --------------------------------------------------------------------------- #
def test_F_orchestrator_gate_refuses_inconsistent_ready(monkeypatch, scripted_agent):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-F",
                "message": "claims-ready-but-blocked textile shipment",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["layer2"] is None
    assert payload["next_action"] != "SHOW_FACT_PACKAGE"
    assert spy.calls == []
