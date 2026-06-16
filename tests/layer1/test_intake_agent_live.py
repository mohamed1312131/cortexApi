# tests/layer1/test_intake_agent_live.py
"""Live agent tests (real model; skipped without GOOGLE_AI_API_KEY).

These cover the language behavior that used to live in deleted Python:
extraction, geography normalization, profile activation — and the negation
regression that motivated the agent-only rewrite ("ship alone, not inside
equipment" must NOT become contained_in_equipment).
"""
import os

import pytest

from app.schemas import FlagState, RequestedMode
from app.services.layer1.intake_agent import run_intake_agent

pytestmark = pytest.mark.live_llm

requires_key = pytest.mark.skipif(
    not os.getenv("GOOGLE_AI_API_KEY"),
    reason="GOOGLE_AI_API_KEY not set; skipping live intake agent tests",
)


@requires_key
def test_live_agent_extracts_and_activates_profiles():
    turn = run_intake_agent(
        "I need to ship 8 tons of lithium batteries from Shenzhen to Paris by road.",
        case_id="case-live-001",
    )

    intake = turn.intake
    assert intake.core_shipment.weight_kg == 8000
    assert intake.lane.origin_city and intake.lane.origin_city.lower() == "shenzhen"
    assert intake.lane.origin_country == "CN"
    assert intake.lane.destination_country == "FR"
    assert intake.mode.requested_mode == RequestedMode.road
    assert intake.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}
    assert "dangerous_goods" in intake.active_profiles
    assert "lithium_battery" in intake.active_profiles
    assert intake.ready_for_layer_2 is False  # no UN number yet -> blocking


@requires_key
def test_live_agent_handles_negated_packing_config():
    """The walk-01 regression: deterministic regex turned 'ship alone, not inside
    equipment' into contained_in_equipment. The agent must read the negation."""
    from app.schemas import ValidatedShipmentRequest

    previous = ValidatedShipmentRequest.model_validate(
        {
            "case_id": "case-live-002",
            "core_shipment": {"cargo_description": "lithium-ion batteries", "weight_kg": 8000},
            "lane": {
                "origin_raw": "Shenzhen",
                "destination_raw": "Frankfurt",
                "origin_city": "Shenzhen",
                "destination_city": "Frankfurt",
                "origin_country": "CN",
                "destination_country": "DE",
            },
            "mode": {"requested_mode": "air", "candidate_modes": ["air"], "needs_mode_selection": False},
            "cargo_flags": {"dangerous_goods": "yes"},
            "active_profiles": ["dangerous_goods", "lithium_battery"],
            "profiles": {
                "dangerous_goods": {"un_number": "UN3480"},
                "lithium_battery": {
                    "battery_type": None,
                    "packed_with_equipment": None,
                    "state_of_charge_pct": None,
                    "un38_3_available": None,
                },
            },
            "ready_for_layer_2": True,
        }
    )

    turn = run_intake_agent(
        "The batteries ship alone, not inside equipment. State of charge is 30% "
        "and we have the UN38.3 test report.",
        case_id="case-live-002",
        previous_request=previous,
    )

    lithium = turn.intake.profiles["lithium_battery"]
    assert lithium["packed_with_equipment"] == "alone"
    assert lithium["state_of_charge_pct"] == 30
    assert lithium["un38_3_available"] is True
    # merge must not degrade existing facts
    assert turn.intake.core_shipment.cargo_description == "lithium-ion batteries"
    assert turn.intake.profiles["dangerous_goods"]["un_number"] == "UN3480"
