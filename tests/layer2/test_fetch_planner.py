from app.schemas import (
    CargoFlags,
    EmptyResponseBehavior,
    FallbackPolicy,
    FetchPriority,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan


def _request(
    requested_mode: RequestedMode,
    candidate_modes: list[RequestedMode],
    dangerous_goods: FlagState = FlagState.unknown,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-layer2-plan-001",
        lane=Lane(origin_country="IT", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=requested_mode,
            candidate_modes=candidate_modes,
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
    )


def test_road_request_plans_road_c_fail_fast():
    request = _request(
        requested_mode=RequestedMode.road,
        candidate_modes=[RequestedMode.road],
    )

    plan = build_fetch_plan(request)

    assert plan.case_id == request.case_id
    assert [item.block_id for item in plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    item = plan.items[0]
    assert item.block_id == "ROAD-C"
    assert item.mode == RequestedMode.road
    assert item.priority == FetchPriority.fail_fast
    assert item.empty_behavior == EmptyResponseBehavior.fail_fast_unknown
    assert item.fallback_policy == FallbackPolicy.return_unknown
    required_fields = {required.field for required in item.required_inputs}
    assert "lane.origin_country" in required_fields
    assert "lane.destination_country" in required_fields

    road_a = plan.items[1]
    assert road_a.priority == FetchPriority.required
    assert road_a.empty_behavior == EmptyResponseBehavior.hard_unknown
    assert road_a.fallback_policy == FallbackPolicy.return_unknown

    road_b = plan.items[2]
    assert road_b.priority == FetchPriority.required
    assert road_b.empty_behavior == EmptyResponseBehavior.soft_unknown
    assert road_b.fallback_policy == FallbackPolicy.return_unknown
    required_fields = {required.field for required in road_b.required_inputs}
    assert "core_shipment.weight_kg" in required_fields
    assert "core_shipment.volume_cbm" in required_fields
    assert "core_shipment.dimensions" in required_fields

    road_f = plan.items[3]
    assert road_f.priority == FetchPriority.optional
    assert road_f.empty_behavior == EmptyResponseBehavior.planning_unknown
    assert road_f.fallback_policy == FallbackPolicy.return_planning_only
    required_fields = {required.field for required in road_f.required_inputs}
    assert "lane.origin_country" in required_fields
    assert "lane.destination_country" in required_fields
    assert "commercial.incoterm" in required_fields
    assert "commercial.ready_date" in required_fields
    assert "commercial.deadline" in required_fields

    road_cost = plan.items[4]
    assert road_cost.priority == FetchPriority.optional
    assert road_cost.empty_behavior == EmptyResponseBehavior.planning_unknown
    assert road_cost.fallback_policy == FallbackPolicy.return_planning_only
    required_fields = {required.field for required in road_cost.required_inputs}
    assert "lane.origin_country" in required_fields
    assert "lane.destination_country" in required_fields
    assert "core_shipment.weight_kg" in required_fields
    assert "core_shipment.volume_cbm" in required_fields
    assert "commercial.incoterm" in required_fields


def test_unknown_mode_with_road_candidate_plans_road_c():
    request = _request(
        requested_mode=RequestedMode.unknown,
        candidate_modes=[RequestedMode.road],
    )

    plan = build_fetch_plan(request)

    assert [item.block_id for item in plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert all(item.mode != RequestedMode.unknown for item in plan.items)


def test_unknown_mode_with_sea_and_road_candidates_plans_both():
    request = _request(
        requested_mode=RequestedMode.unknown,
        candidate_modes=[RequestedMode.sea, RequestedMode.road],
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
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert all(item.mode != RequestedMode.unknown for item in plan.items)


def test_unknown_mode_without_road_candidate_does_not_plan_road_c():
    request = _request(
        requested_mode=RequestedMode.unknown,
        candidate_modes=[RequestedMode.sea, RequestedMode.air],
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
        "AIR-C",
        "AIR-D",
        "AIR-A",
        "AIR-B",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    ]
    assert "ROAD-C" not in [item.block_id for item in plan.items]


def test_sea_request_plans_sea_c():
    request = _request(
        requested_mode=RequestedMode.sea,
        candidate_modes=[RequestedMode.sea],
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
    item = plan.items[0]
    assert item.block_id == "SEA-C"
    assert item.mode == RequestedMode.sea
    assert item.priority == FetchPriority.fail_fast
    assert item.empty_behavior == EmptyResponseBehavior.fail_fast_unknown
    assert item.fallback_policy == FallbackPolicy.return_unknown
    required_fields = {required.field for required in item.required_inputs}
    assert "lane.origin_city" in required_fields
    assert "lane.origin_country" in required_fields

    sea_d = plan.items[1]
    assert sea_d.block_id == "SEA-D"
    assert sea_d.mode == RequestedMode.sea
    assert sea_d.priority == FetchPriority.required
    assert sea_d.empty_behavior == EmptyResponseBehavior.soft_unknown
    assert sea_d.fallback_policy == FallbackPolicy.return_unknown
    required_fields = {required.field for required in sea_d.required_inputs}
    assert "lane.origin_country" in required_fields
    assert "lane.destination_country" in required_fields
    assert "cargo_flags" in required_fields

    sea_a = plan.items[2]
    assert sea_a.block_id == "SEA-A"
    assert sea_a.mode == RequestedMode.sea
    assert sea_a.priority == FetchPriority.required
    assert sea_a.empty_behavior == EmptyResponseBehavior.hard_unknown
    assert sea_a.fallback_policy == FallbackPolicy.return_unknown

    sea_b = plan.items[3]
    assert sea_b.block_id == "SEA-B"
    assert sea_b.mode == RequestedMode.sea
    assert sea_b.priority == FetchPriority.required
    assert sea_b.empty_behavior == EmptyResponseBehavior.soft_unknown
    assert sea_b.fallback_policy == FallbackPolicy.return_unknown
    required_fields = {required.field for required in sea_b.required_inputs}
    assert "core_shipment.weight_kg" in required_fields
    assert "core_shipment.volume_cbm" in required_fields
    assert "core_shipment.dimensions" in required_fields

    sea_f = plan.items[4]
    assert sea_f.block_id == "SEA-F"
    assert sea_f.mode == RequestedMode.sea
    assert sea_f.priority == FetchPriority.optional
    assert sea_f.empty_behavior == EmptyResponseBehavior.planning_unknown
    assert sea_f.fallback_policy == FallbackPolicy.return_planning_only
    required_fields = {required.field for required in sea_f.required_inputs}
    assert "core_shipment.weight_kg" in required_fields
    assert "cargo_flags.dangerous_goods" in required_fields

    sea_i = plan.items[5]
    assert sea_i.block_id == "SEA-I"
    assert sea_i.mode == RequestedMode.sea
    assert sea_i.priority == FetchPriority.required
    assert sea_i.empty_behavior == EmptyResponseBehavior.soft_unknown
    assert sea_i.fallback_policy == FallbackPolicy.return_unknown
    required_fields = {required.field for required in sea_i.required_inputs}
    assert "lane.origin_country" in required_fields
    assert "lane.destination_country" in required_fields
    assert "lane.origin_city" in required_fields
    assert "lane.destination_city" in required_fields
    assert "commercial.ready_date" in required_fields
    assert "commercial.deadline" in required_fields

    sea_cost = plan.items[6]
    assert sea_cost.block_id == "SEA-COST"
    assert sea_cost.mode == RequestedMode.sea
    assert sea_cost.priority == FetchPriority.optional
    assert sea_cost.empty_behavior == EmptyResponseBehavior.planning_unknown
    assert sea_cost.fallback_policy == FallbackPolicy.return_planning_only
    required_fields = {required.field for required in sea_cost.required_inputs}
    assert "lane.origin_country" in required_fields
    assert "lane.destination_country" in required_fields
    assert "core_shipment.weight_kg" in required_fields
    assert "core_shipment.volume_cbm" in required_fields
    assert "commercial.incoterm" in required_fields


def test_sea_request_non_dg_skips_sea_a():
    request = _request(
        requested_mode=RequestedMode.sea,
        candidate_modes=[RequestedMode.sea],
        dangerous_goods=FlagState.no,
    )

    plan = build_fetch_plan(request)

    assert [item.block_id for item in plan.items] == [
        "SEA-C",
        "SEA-D",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]
