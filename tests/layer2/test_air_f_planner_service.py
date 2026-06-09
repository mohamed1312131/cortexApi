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


def _air_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-f-service",
        lane=Lane(
            origin_city="Paris",
            origin_country="FR",
            destination_city="New York",
            destination_country="US",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
            needs_mode_selection=False,
        ),
        cargo_flags=_flags(),
        core_shipment=CoreShipment(
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
        ),
        commercial=Commercial(incoterm="DAP"),
    )


def test_air_request_plans_air_f_after_air_e():
    plan = build_fetch_plan(_air_request())
    block_ids = [item.block_id for item in plan.items]

    assert "AIR-F" in block_ids
    assert block_ids.index("AIR-E") < block_ids.index("AIR-F")
    assert block_ids.index("AIR-F") < block_ids.index("AIR-H")
    assert block_ids.index("AIR-H") < block_ids.index("AIR-I")


def test_layer2_service_air_runs_air_f():
    package = build_fact_package_for_request(_air_request())
    response_blocks = [response.block_id for response in package.block_responses]

    assert "AIR-F" in response_blocks
    assert "AIR-H" in response_blocks
    assert "AIR-I" in response_blocks
    assert RequestedMode.air in package.derived_rollup.modes_covered
