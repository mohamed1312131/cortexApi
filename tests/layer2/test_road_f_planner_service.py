from app.schemas import (
    CargoFlags,
    Commercial,
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
        case_id="case-road-f-service",
        lane=Lane(
            origin_city="Milan",
            origin_country="IT",
            destination_city="Paris",
            destination_country="FR",
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
            cargo_description="industrial spare parts",
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


def test_road_request_plans_road_f_after_road_b_when_road_c_not_blocked():
    plan = build_fetch_plan(_road_request())
    blocks = [item.block_id for item in plan.items]

    assert blocks == ["ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST"]
    assert blocks.index("ROAD-C") < blocks.index("ROAD-B")
    assert blocks.index("ROAD-B") < blocks.index("ROAD-F")


def test_layer2_service_road_runs_road_f_after_road_c_without_gate():
    package = build_fact_package_for_request(_road_request())
    blocks = [response.block_id for response in package.block_responses]

    assert blocks == ["ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST"]
    assert blocks.index("ROAD-C") < blocks.index("ROAD-F")
    road_f = next(
        response for response in package.block_responses if response.block_id == "ROAD-F"
    )
    assert road_f.data["road_preparation_status"] == (
        "planning_only_requires_carrier_border_validation"
    )
    assert RequestedMode.road in package.derived_rollup.modes_covered
