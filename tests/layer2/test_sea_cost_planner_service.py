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


def _sea_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-cost-service",
        lane=Lane(
            origin_city="Shanghai",
            origin_country="CN",
            destination_city="Marseille",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
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
            cargo_description="machinery parts",
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
            quantity=3,
            packaging="crates",
        ),
        commercial=Commercial(
            incoterm="FOB",
            ready_date="2026-06-10",
            deadline="2026-07-10",
        ),
    )


def test_sea_request_plans_sea_cost_last():
    plan = build_fetch_plan(_sea_request())

    assert plan.items[-1].block_id == "SEA-COST"


def test_layer2_service_sea_runs_sea_cost():
    package = build_fact_package_for_request(_sea_request())
    response_blocks = [response.block_id for response in package.block_responses]

    assert "SEA-COST" in response_blocks
    assert RequestedMode.sea in package.derived_rollup.modes_covered
