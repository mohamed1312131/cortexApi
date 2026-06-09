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
        case_id="case-air-a-service",
        lane=Lane(
            origin_city="Shanghai",
            origin_country="CN",
            destination_city="Paris",
            destination_country="FR",
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


def test_air_request_with_dg_plans_air_a():
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
    ]


def test_air_request_non_dg_does_not_plan_air_a():
    plan = build_fetch_plan(_air_request(dangerous_goods=FlagState.no))

    assert [item.block_id for item in plan.items] == [
        "AIR-C",
        "AIR-D",
        "AIR-B",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    ]
    assert "AIR-A" not in [item.block_id for item in plan.items]


def test_layer2_service_air_dg_runs_air_a():
    request = _air_request(
        dangerous_goods=FlagState.yes,
        un_number="UN3480",
    )

    package = build_fact_package_for_request(request)

    assert "AIR-C" in [response.block_id for response in package.block_responses]
    assert "AIR-D" in [response.block_id for response in package.block_responses]
    assert "AIR-A" in [response.block_id for response in package.block_responses]
    assert "AIR-B" in [response.block_id for response in package.block_responses]
    assert "AIR-E" in [response.block_id for response in package.block_responses]
    assert "AIR-F" in [response.block_id for response in package.block_responses]
    assert "AIR-H" in [response.block_id for response in package.block_responses]
    assert "AIR-I" in [response.block_id for response in package.block_responses]
