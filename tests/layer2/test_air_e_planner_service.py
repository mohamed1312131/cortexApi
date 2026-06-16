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


def _air_request(
    *,
    dangerous_goods: FlagState,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-air-e-service",
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
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        core_shipment=CoreShipment(
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
        ),
        profiles=profiles,
    )


def test_air_request_plans_air_c_air_a_air_e_for_dg():
    request = _air_request(
        dangerous_goods=FlagState.yes,
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
        "AIR-COST",
    ]


def test_air_request_non_dg_plans_air_c_air_e():
    plan = build_fetch_plan(_air_request(dangerous_goods=FlagState.no))
    block_ids = [item.block_id for item in plan.items]

    assert block_ids == [
        "AIR-C",
        "AIR-D",
        "AIR-B",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
        "AIR-COST",
    ]
    assert "AIR-A" not in block_ids


def test_layer2_service_air_runs_air_c_air_a_air_e_for_dg():
    request = _air_request(
        dangerous_goods=FlagState.yes,
        un_number="UN3480",
    )

    package = build_fact_package_for_request(request)
    response_blocks = [response.block_id for response in package.block_responses]

    assert "AIR-C" in response_blocks
    assert "AIR-D" in response_blocks
    assert "AIR-A" in response_blocks
    assert "AIR-B" in response_blocks
    assert "AIR-E" in response_blocks
    assert "AIR-F" in response_blocks
    assert "AIR-H" in response_blocks
    assert "AIR-I" in response_blocks
