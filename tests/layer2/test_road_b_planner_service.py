from app.schemas import (
    BlockStatus,
    CargoFlags,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.service import build_fact_package_for_request


def _road_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-road-b-service",
        lane=Lane(origin_country="IT", destination_country="FR"),
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
            quantity=4,
            packaging="pallets",
        ),
    )


def test_road_request_plans_road_b_after_road_c():
    plan = build_fetch_plan(_road_request())
    blocks = [item.block_id for item in plan.items]

    assert blocks == ["ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST"]
    assert blocks.index("ROAD-C") < blocks.index("ROAD-B")


def test_layer2_service_road_runs_road_b_after_road_c_without_gate():
    package = build_fact_package_for_request(_road_request())
    blocks = [response.block_id for response in package.block_responses]

    assert blocks == ["ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST"]
    assert blocks.index("ROAD-C") < blocks.index("ROAD-B")
    road_b = next(
        response for response in package.block_responses if response.block_id == "ROAD-B"
    )
    assert road_b.status in {BlockStatus.found, BlockStatus.unknown}
    assert road_b.data["fit_status"] == "planning_only_requires_carrier_validation"
    assert isinstance(road_b.data["candidate_vehicle_examples"], list)
