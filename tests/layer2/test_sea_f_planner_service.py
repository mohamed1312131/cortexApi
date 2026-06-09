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
        case_id="case-sea-f-service",
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
        cargo_flags=CargoFlags(dangerous_goods=FlagState.no),
        core_shipment=CoreShipment(
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(incoterm="FOB"),
    )


def test_sea_request_plans_sea_c_then_sea_b_then_sea_f():
    plan = build_fetch_plan(_sea_request())

    assert [item.block_id for item in plan.items] == [
        "SEA-C",
        "SEA-D",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]


def test_layer2_service_sea_runs_sea_c_sea_b_sea_f():
    package = build_fact_package_for_request(_sea_request())

    assert [response.block_id for response in package.block_responses] == [
        "SEA-C",
        "SEA-D",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]
