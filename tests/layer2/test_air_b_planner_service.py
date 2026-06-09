from app.schemas import (
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
        case_id="case-air-b-service",
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
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
        ),
        profiles=profiles,
    )


def test_air_request_with_special_flag_plans_air_c_air_b_air_e():
    request = _air_request(
        cargo_flags=_flags(temperature_controlled=FlagState.yes)
    )

    plan = build_fetch_plan(request)
    block_ids = [item.block_id for item in plan.items]

    assert block_ids == [
        "AIR-C",
        "AIR-D",
        "AIR-B",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    ]
    assert block_ids.index("AIR-C") < block_ids.index("AIR-B")
    assert block_ids.index("AIR-C") < block_ids.index("AIR-D")
    assert block_ids.index("AIR-D") < block_ids.index("AIR-B")
    assert block_ids.index("AIR-B") < block_ids.index("AIR-E")
    assert "AIR-A" not in block_ids


def test_air_request_with_dg_and_special_flag_plans_air_c_air_a_air_b_air_e():
    request = _air_request(
        cargo_flags=_flags(
            dangerous_goods=FlagState.yes,
            temperature_controlled=FlagState.yes,
        ),
        un_number="UN3480",
    )

    plan = build_fetch_plan(request)

    assert [item.block_id for item in plan.items] == [
        "AIR-C",
        "AIR-D",
        "AIR-A",
        "AIR-B",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    ]


def test_layer2_service_air_special_runs_air_b():
    request = _air_request(
        cargo_flags=_flags(temperature_controlled=FlagState.yes)
    )

    package = build_fact_package_for_request(request)
    response_blocks = [response.block_id for response in package.block_responses]

    assert "AIR-D" in response_blocks
    assert "AIR-B" in response_blocks
    assert "AIR-F" in response_blocks
    assert "AIR-H" in response_blocks
    assert "AIR-I" in response_blocks
    assert RequestedMode.air in package.derived_rollup.modes_covered
