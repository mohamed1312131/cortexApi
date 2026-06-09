from app.schemas import (
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.service import build_fact_package_for_request


def test_sea_request_plans_sea_c_then_sea_b_then_sea_f():
    request = ValidatedShipmentRequest(
        case_id="case-sea-plan",
        lane=Lane(origin_city="Shanghai", origin_country="CN"),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
        ),
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
    assert plan.items[0].block_id == "SEA-C"
    assert plan.items[0].mode == RequestedMode.sea


def test_layer2_service_sea_origin_port_returns_fact_package():
    request = ValidatedShipmentRequest(
        case_id="case-sea-shanghai-marseille",
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
    )

    package = build_fact_package_for_request(request)

    assert "SEA-C" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-D" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-A" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-B" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-F" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-I" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-COST" in [item.block_id for item in package.fetch_plan.items]
    assert "SEA-C" in [response.block_id for response in package.block_responses]
    assert "SEA-D" in [response.block_id for response in package.block_responses]
    assert "SEA-A" in [response.block_id for response in package.block_responses]
    assert "SEA-B" in [response.block_id for response in package.block_responses]
    assert "SEA-F" in [response.block_id for response in package.block_responses]
    assert "SEA-I" in [response.block_id for response in package.block_responses]
    assert "SEA-COST" in [response.block_id for response in package.block_responses]
    assert RequestedMode.sea in package.derived_rollup.modes_covered
