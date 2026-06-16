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


def _air_request(
    *,
    cargo_flags: CargoFlags,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-air-d-service",
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
        cargo_flags=cargo_flags,
        core_shipment=CoreShipment(
            cargo_description="electronics spare parts",
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
            quantity=2,
            packaging="cartons",
        ),
        commercial=Commercial(
            incoterm="DAP",
            ready_date="2026-06-10",
            deadline="2026-06-12",
        ),
        profiles=profiles,
    )


def test_air_request_plans_air_d_after_air_c_before_air_a():
    request = _air_request(
        cargo_flags=_flags(dangerous_goods=FlagState.yes),
        un_number="UN3480",
    )

    plan = build_fetch_plan(request)
    block_ids = [item.block_id for item in plan.items]

    assert block_ids.index("AIR-C") < block_ids.index("AIR-D")
    assert block_ids.index("AIR-D") < block_ids.index("AIR-A")


def test_air_request_non_dg_plans_air_c_air_d_air_e_air_f_air_h_air_i():
    request = _air_request(cargo_flags=_flags())

    plan = build_fetch_plan(request)
    block_ids = [item.block_id for item in plan.items]

    assert block_ids == [
        "AIR-C",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
        "AIR-COST",
    ]
    assert "AIR-A" not in block_ids
    assert "AIR-B" not in block_ids


def test_layer2_service_air_runs_air_d():
    package = build_fact_package_for_request(_air_request(cargo_flags=_flags()))
    response_blocks = [response.block_id for response in package.block_responses]

    assert "AIR-D" in response_blocks
    assert RequestedMode.air in package.derived_rollup.modes_covered
