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


def _flags(**overrides: FlagState) -> CargoFlags:
    values = {
        "dangerous_goods": FlagState.no,
        "temperature_controlled": FlagState.no,
        "oversized": FlagState.no,
        "high_value": FlagState.no,
        "pharma": FlagState.no,
        "food_perishable": FlagState.no,
        "live_animals": FlagState.no,
    }
    values.update(overrides)
    return CargoFlags(**values)


def _sea_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-i-service",
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
        cargo_flags=_flags(),
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


def test_sea_request_plans_sea_i_after_sea_f():
    plan = build_fetch_plan(_sea_request())
    block_ids = [item.block_id for item in plan.items]

    assert "SEA-I" in block_ids
    assert block_ids.index("SEA-F") < block_ids.index("SEA-I")
    assert block_ids.index("SEA-I") < block_ids.index("SEA-COST")


def test_layer2_service_sea_runs_sea_i():
    package = build_fact_package_for_request(_sea_request())
    response_blocks = [response.block_id for response in package.block_responses]

    assert "SEA-I" in response_blocks
    assert "SEA-COST" in response_blocks
    assert RequestedMode.sea in package.derived_rollup.modes_covered
