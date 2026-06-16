"""Offline tests for the agent-only Layer 1.

The model is faked; these tests cover the plumbing contract around the agent:
JSON parsing + one retry, case-id injection, case persistence, the mechanical
diff (changed_fields), rerun-scope mapping, and case lifecycle (status, version,
start_new_case). No language behavior is asserted here — that belongs to the
agent and is covered by the live tests.
"""

from __future__ import annotations

import json

import pytest

from app.schemas import CaseStatus, IntakeDecision
from app.services.layer1 import intake_agent
from app.services.layer1.case_state_manager import InMemoryCaseStateStore
from app.services.layer1.graph import Layer1AgentIntake
from app.services.layer1.intake_agent import (
    AgentTurn,
    IntakeAgentError,
    run_intake_agent,
)


def _turn_dict(**overrides) -> dict:
    intake = {
        "user_goal": {"primary_goal": "find_preparation_paths", "priority": "unknown", "deadline_sensitivity": "unknown"},
        "core_shipment": {"cargo_description": "textiles", "weight_kg": 500.0, "volume_cbm": None, "dimensions": None, "quantity": None, "packaging": None},
        "lane": {"origin_raw": "Milan", "destination_raw": "Paris", "origin_country": "IT", "destination_country": "FR", "origin_city": "Milan", "destination_city": "Paris"},
        "mode": {"requested_mode": "road", "candidate_modes": ["road"], "needs_mode_selection": False},
        "cargo_flags": {"dangerous_goods": "no", "temperature_controlled": "unknown", "oversized": "unknown", "high_value": "unknown", "pharma": "unknown", "food_perishable": "unknown", "live_animals": "unknown"},
        "active_profiles": ["general_cargo"],
        "profiles": {"general_cargo": {}},
        "commercial": {"incoterm": None, "cargo_value": None, "currency": None, "ready_date": None, "deadline": None},
        "facts_from_user": {"cargo_description": "textiles", "weight_kg": 500.0},
        "inferred_flags": {},
        "missing_fields": {"blocking": [], "high_value": ["volume or dimensions"], "can_wait": ["incoterm"]},
        "questions_to_user": [],
        "ready_for_layer_2": True,
        "field_confidence": {"cargo_description": 0.9, "weight_kg": 0.9},
        "intake_quality_score": 0.93,
    }
    intake.update(overrides.pop("intake", {}))
    turn = {
        "case_action": "create_new_case",
        "intent": "shipment_readiness",
        "decision": "ready_for_layer_2_with_unknowns",
        "assistant_message": "Textiles, 500 kg, Milan to Paris. Moving to the data check.",
        "intake": intake,
    }
    turn.update(overrides)
    return turn


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    """Returns scripted responses in order; records the prompts it received."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> _FakeMessage:
        self.prompts.append(prompt)
        return _FakeMessage(self.responses.pop(0))


# --------------------------------------------------------------------------- #
# run_intake_agent: parsing, retry, case-id injection
# --------------------------------------------------------------------------- #
def test_agent_turn_parses_and_injects_case_id():
    model = _FakeModel([json.dumps(_turn_dict())])

    turn = run_intake_agent("ship textiles", case_id="SHIP-TEST0001", model=model)

    assert isinstance(turn, AgentTurn)
    assert turn.intake.case_id == "SHIP-TEST0001"
    assert turn.decision is IntakeDecision.ready_for_layer_2_with_unknowns
    assert len(model.prompts) == 1


def test_agent_retries_once_with_error_feedback_then_succeeds():
    model = _FakeModel(["this is not json", json.dumps(_turn_dict())])

    turn = run_intake_agent("ship textiles", case_id="SHIP-TEST0002", model=model)

    assert turn.intake.case_id == "SHIP-TEST0002"
    assert len(model.prompts) == 2
    assert "previous_attempt_error" in model.prompts[1]


def test_agent_fails_after_two_invalid_outputs():
    model = _FakeModel(["garbage", "still garbage"])

    with pytest.raises(IntakeAgentError):
        run_intake_agent("ship textiles", case_id="SHIP-TEST0003", model=model)


def test_agent_rejects_output_without_intake_object():
    payload = _turn_dict()
    payload.pop("intake")
    model = _FakeModel([json.dumps(payload), json.dumps(payload)])

    with pytest.raises(IntakeAgentError):
        run_intake_agent("ship textiles", case_id="SHIP-TEST0004", model=model)


def test_agent_requires_a_configured_model(monkeypatch):
    monkeypatch.setattr(intake_agent, "get_chat_model", lambda **_kwargs: None)

    with pytest.raises(IntakeAgentError):
        run_intake_agent("ship textiles", case_id="SHIP-TEST0005")


def test_agent_strips_markdown_fences():
    fenced = "```json\n" + json.dumps(_turn_dict()) + "\n```"
    model = _FakeModel([fenced])

    turn = run_intake_agent("ship textiles", case_id="SHIP-TEST0006", model=model)

    assert turn.intake.core_shipment.cargo_description == "textiles"


# --------------------------------------------------------------------------- #
# handle_message: persistence, diff, rerun scope, lifecycle
# --------------------------------------------------------------------------- #
def _service(responses: list[str]) -> tuple[Layer1AgentIntake, InMemoryCaseStateStore]:
    store = InMemoryCaseStateStore()
    service = Layer1AgentIntake(store=store, model=_FakeModel(responses))
    return service, store


def test_first_message_creates_case_and_builds_result():
    service, store = _service([json.dumps(_turn_dict())])

    result = service.handle_message(message="Ship 500 kg textiles Milan to Paris", conversation_id="conv-1")

    assert result.case_id.startswith("SHIP-")
    assert result.ready_for_layer_2 is True
    assert result.requires_layer_2_rerun is True
    assert "core_shipment.cargo_description" in result.changed_fields
    assert "lane.origin_city" in result.changed_fields
    assert "active_profiles" in result.changed_fields
    assert "chargeable_weight" in result.rerun_scope["scope"]
    assert "node_resolution" in result.rerun_scope["scope"]

    saved = store.get_active_for_conversation("conv-1")
    assert saved is not None
    assert saved.case_id == result.case_id
    assert saved.status is CaseStatus.ready_for_layer_2
    assert saved.shipment_request_version == 1
    assert "User:" in saved.conversation_summary


def test_follow_up_diffs_only_what_changed_and_bumps_version():
    first = _turn_dict()
    second = _turn_dict(
        case_action="update_existing_case",
        intent="follow_up_update",
        decision="update_case_and_rerun",
        intake={"core_shipment": {"cargo_description": "textiles", "weight_kg": 900.0, "volume_cbm": None, "dimensions": None, "quantity": None, "packaging": None}},
    )
    service, store = _service([json.dumps(first), json.dumps(second)])

    first_result = service.handle_message(message="Ship 500 kg textiles Milan to Paris", conversation_id="conv-2")
    second_result = service.handle_message(message="Actually it's 900 kg", conversation_id="conv-2")

    assert second_result.case_id == first_result.case_id
    assert second_result.changed_fields == ["core_shipment.weight_kg"]
    assert second_result.requires_layer_2_rerun is True
    assert "chargeable_weight" in second_result.rerun_scope["scope"]
    assert store.get(second_result.case_id).shipment_request_version == 2


def test_unchanged_follow_up_requires_no_rerun():
    same = _turn_dict(
        case_action="update_existing_case",
        intent="follow_up_update",
        decision="update_case_and_rerun",
    )
    service, _ = _service([json.dumps(_turn_dict()), json.dumps(same)])

    service.handle_message(message="Ship textiles Milan to Paris", conversation_id="conv-3")
    result = service.handle_message(message="thanks", conversation_id="conv-3")

    assert result.changed_fields == []
    assert result.requires_layer_2_rerun is False
    assert result.rerun_scope["scope"] == []


def test_ask_user_turn_is_not_ready_and_waits():
    turn = _turn_dict(
        decision="ask_user",
        intake={
            "missing_fields": {"blocking": ["weight or quantity"], "high_value": [], "can_wait": []},
            "questions_to_user": [
                {"question": "What is the weight?", "reason": "Needed for fit.", "field_target": "core_shipment.weight_kg"}
            ],
            "ready_for_layer_2": False,
        },
    )
    service, store = _service([json.dumps(turn)])

    result = service.handle_message(message="ship textiles", conversation_id="conv-4")

    assert result.ready_for_layer_2 is False
    assert result.requires_layer_2_rerun is False
    assert result.questions_to_user[0].field_target == "core_shipment.weight_kg"
    assert store.get(result.case_id).status is CaseStatus.waiting_for_user_clarification
    assert store.get(result.case_id).last_missing_questions == ["core_shipment.weight_kg"]


def test_start_new_case_mints_a_fresh_case_id():
    first = _turn_dict()
    fresh = _turn_dict(case_action="start_new_case", decision="start_new_case")
    service, store = _service([json.dumps(first), json.dumps(fresh)])

    first_result = service.handle_message(message="Ship textiles Milan to Paris", conversation_id="conv-5")
    second_result = service.handle_message(message="New shipment please", conversation_id="conv-5")

    assert second_result.case_id != first_result.case_id
    assert store.get_active_for_conversation("conv-5").case_id == second_result.case_id
