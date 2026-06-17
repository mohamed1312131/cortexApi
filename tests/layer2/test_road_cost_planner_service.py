from app.schemas import (
    BlockStatus,
    CargoFlags,
    Commercial,
    CoreShipment,
    FlagState,
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.service import build_fact_package_for_request


def _road_request(
    origin_country: str = "IT",
    destination_country: str = "FR",
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=f"case-road-cost-{origin_country.lower()}-{destination_country.lower()}",
        lane=Lane(
            origin_city="Milan",
            origin_country=origin_country,
            destination_city="Paris",
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(
            dangerous_goods=FlagState.no,
            temperature_controlled=FlagState.no,
            oversized=FlagState.no,
            high_value=FlagState.no,
            pharma=FlagState.no,
            food_perishable=FlagState.no,
            live_animals=FlagState.no,
        ),
        core_shipment=CoreShipment(
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(
            incoterm="DAP",
            ready_date="2026-06-10",
            deadline="2026-06-12",
        ),
    )


def test_road_request_plans_road_cost_last():
    plan = build_fetch_plan(_road_request())

    assert [item.block_id for item in plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert plan.items[-1].block_id == "ROAD-COST"


def test_layer2_service_road_runs_road_cost_when_road_c_not_blocked():
    package = build_fact_package_for_request(_road_request())

    assert "ROAD-COST" in [response.block_id for response in package.block_responses]
    road_cost = next(
        response
        for response in package.block_responses
        if response.block_id == "ROAD-COST"
    )
    assert road_cost.status in {BlockStatus.found, BlockStatus.unknown}
    assert road_cost.data["cost_status"] == "planning_reference_not_a_quote"
    assert RequestedMode.road in package.derived_rollup.modes_covered


def test_layer2_service_road_runs_road_cost_even_when_road_c_blocks():
    package = build_fact_package_for_request(_road_request("CN", "FR"))

    road_c = next(
        response for response in package.block_responses if response.block_id == "ROAD-C"
    )
    assert any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in road_c.hard_gates
    )
    road_cost = next(
        response
        for response in package.block_responses
        if response.block_id == "ROAD-COST"
    )
    # cascade-skip removed: cost reference still runs for a blocked corridor
    assert road_cost.status != BlockStatus.skipped
