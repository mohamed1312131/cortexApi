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
from app.services.layer2.connectors.sea_a_connector import _find_record, _load_records
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.service import build_fact_package_for_request


def _known_un_number() -> str:
    if _find_record(_load_records(), "UN3480") is not None:
        return "UN3480"
    return "UN1410"


def _sea_request(
    *,
    dangerous_goods: FlagState,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-sea-a-service",
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
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        core_shipment=CoreShipment(
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(incoterm="FOB"),
        profiles=profiles,
    )


def test_sea_request_with_dg_plans_sea_c_sea_a_sea_b_sea_f():
    request = _sea_request(
        dangerous_goods=FlagState.yes,
        un_number=_known_un_number(),
    )

    plan = build_fetch_plan(request)

    assert [item.block_id for item in plan.items] == [
        "SEA-C",
        "SEA-D",
        "SEA-A",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]


def test_sea_request_non_dg_does_not_plan_sea_a():
    plan = build_fetch_plan(_sea_request(dangerous_goods=FlagState.no))

    assert "SEA-A" not in [item.block_id for item in plan.items]


def test_layer2_service_sea_dg_runs_sea_a():
    request = _sea_request(
        dangerous_goods=FlagState.yes,
        un_number=_known_un_number(),
    )

    package = build_fact_package_for_request(request)

    assert "SEA-D" in [response.block_id for response in package.block_responses]
    assert "SEA-A" in [response.block_id for response in package.block_responses]
    assert "SEA-I" in [response.block_id for response in package.block_responses]
    assert "SEA-COST" in [response.block_id for response in package.block_responses]
