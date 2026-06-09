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


def _sea_request(
    *,
    cargo_flags: CargoFlags,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-sea-d-service",
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
        cargo_flags=cargo_flags,
        core_shipment=CoreShipment(
            cargo_description="machinery parts",
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
            quantity=3,
            packaging="crates",
        ),
        commercial=Commercial(incoterm="FOB"),
        profiles=profiles,
    )


def test_sea_request_plans_sea_d_after_sea_c_before_sea_a_for_dg():
    request = _sea_request(
        cargo_flags=_flags(dangerous_goods=FlagState.yes),
        un_number="UN3480",
    )

    plan = build_fetch_plan(request)
    block_ids = [item.block_id for item in plan.items]

    assert block_ids.index("SEA-C") < block_ids.index("SEA-D")
    assert block_ids.index("SEA-D") < block_ids.index("SEA-A")


def test_sea_request_non_dg_plans_sea_c_sea_d_sea_b_sea_f():
    request = _sea_request(cargo_flags=_flags())

    plan = build_fetch_plan(request)
    block_ids = [item.block_id for item in plan.items]

    assert block_ids == [
        "SEA-C",
        "SEA-D",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]
    assert "SEA-A" not in block_ids


def test_layer2_service_sea_runs_sea_d():
    package = build_fact_package_for_request(_sea_request(cargo_flags=_flags()))
    response_blocks = [response.block_id for response in package.block_responses]

    assert "SEA-D" in response_blocks
    assert "SEA-I" in response_blocks
    assert "SEA-COST" in response_blocks
    assert RequestedMode.sea in package.derived_rollup.modes_covered
