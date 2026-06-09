from app.schemas import (
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_executor import execute_fetch_plan
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.registry import get_connector


def test_registry_contains_road_blocks():
    assert get_connector("AIR-A") is not None
    assert get_connector("AIR-B") is not None
    assert get_connector("AIR-C") is not None
    assert get_connector("AIR-D") is not None
    assert get_connector("AIR-E") is not None
    assert get_connector("AIR-F") is not None
    assert get_connector("AIR-H") is not None
    assert get_connector("AIR-I") is not None
    assert get_connector("ROAD-C") is not None
    assert get_connector("ROAD-A") is not None
    assert get_connector("ROAD-B") is not None
    assert get_connector("ROAD-F") is not None
    assert get_connector("ROAD-COST") is not None
    assert get_connector("SEA-C") is not None
    assert get_connector("SEA-D") is not None
    assert get_connector("SEA-A") is not None
    assert get_connector("SEA-B") is not None
    assert get_connector("SEA-F") is not None
    assert get_connector("SEA-I") is not None
    assert get_connector("SEA-COST") is not None


def test_registry_unknown_block_returns_none():
    assert get_connector("UNKNOWN") is None


def test_executor_still_uses_registry_for_road_c():
    request = ValidatedShipmentRequest(
        case_id="case-registry-cn-fr",
        lane=Lane(origin_country="CN", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
    )
    plan = build_fetch_plan(request)

    responses = execute_fetch_plan(request, plan)

    road_c_response = next(
        response for response in responses if response.block_id == "ROAD-C"
    )
    assert any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in road_c_response.hard_gates
    )
