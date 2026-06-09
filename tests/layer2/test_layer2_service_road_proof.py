from app.schemas import (
    BlockStatus,
    CompletenessStatus,
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.service import build_fact_package_for_request


def test_layer2_service_milan_paris_road_not_blocked():
    request = ValidatedShipmentRequest(
        case_id="case-road-it-fr",
        lane=Lane(
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
    )

    package = build_fact_package_for_request(request)

    assert package.case_id == request.case_id
    assert [item.block_id for item in package.fetch_plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert package.fetch_plan.items[0].block_id == "ROAD-C"
    assert len(package.block_responses) == 5
    response = package.block_responses[0]
    assert response.block_id == "ROAD-C"
    assert response.status == BlockStatus.found
    assert not any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in package.derived_rollup.hard_gates
    )
    assert package.completeness.status in {
        CompletenessStatus.complete_enough,
        CompletenessStatus.incomplete_but_usable,
    }


def test_layer2_service_shenzhen_paris_road_blocked_at_corridor_gate():
    request = ValidatedShipmentRequest(
        case_id="case-road-cn-fr",
        lane=Lane(
            origin_city="Shenzhen",
            destination_city="Paris",
            origin_country="CN",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
        ),
    )

    package = build_fact_package_for_request(request)

    assert package.case_id == request.case_id
    assert [item.block_id for item in package.fetch_plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert package.fetch_plan.items[0].block_id == "ROAD-C"
    assert len(package.block_responses) == 5
    response = package.block_responses[0]
    assert response.block_id == "ROAD-C"
    assert response.status == BlockStatus.found
    assert [item.status for item in package.block_responses[1:]] == [
        BlockStatus.skipped,
        BlockStatus.skipped,
        BlockStatus.skipped,
        BlockStatus.skipped,
    ]
    assert package.completeness.status == CompletenessStatus.blocked
    blocking_triggered_gates = [
        gate
        for gate in package.derived_rollup.hard_gates
        if gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
    ]
    assert len(blocking_triggered_gates) == 1
    gate = blocking_triggered_gates[0]
    assert gate.gate_id == "ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL"
    assert gate.source_block == "ROAD-C"
    assert gate.basis == response.data["corridor_viability"]
