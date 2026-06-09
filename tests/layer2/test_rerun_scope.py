from app.schemas import (
    CargoFlags,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.rerun_scope import (
    block_dependencies,
    impacted_blocks_for_changed_fields,
    impacted_fetch_plan_items,
    summarize_rerun_scope,
)


def _request(
    requested_mode: RequestedMode,
    candidate_modes: list[RequestedMode],
    dangerous_goods: FlagState = FlagState.unknown,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-layer2-rerun-scope-001",
        lane=Lane(
            origin_city="Tunis",
            origin_country="TN",
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=requested_mode,
            candidate_modes=candidate_modes,
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
    )


def test_impacted_blocks_for_lane_country_change():
    impacted = impacted_blocks_for_changed_fields(["lane.origin_country"])

    assert {"ROAD-C", "ROAD-F", "ROAD-COST", "SEA-D", "AIR-F"}.issubset(impacted)


def test_impacted_blocks_for_dangerous_goods_change():
    impacted = impacted_blocks_for_changed_fields(["cargo_flags.dangerous_goods"])

    assert {"ROAD-A", "SEA-A", "AIR-A"}.issubset(impacted)
    assert "SEA-C" not in impacted


def test_parent_prefix_change_impacts_children():
    impacted = impacted_blocks_for_changed_fields(["cargo_flags"])

    assert len(impacted) > 10
    assert {"ROAD-A", "SEA-A", "AIR-A"}.issubset(impacted)


def test_unknown_field_has_no_impact():
    impacted = impacted_blocks_for_changed_fields(["random.field"])

    assert impacted == set()


def test_block_dependencies_reverse_map():
    road_c_dependencies = block_dependencies("ROAD-C")
    air_e_dependencies = block_dependencies("AIR-E")

    assert "lane.origin_country" in road_c_dependencies
    assert "lane.destination_country" in road_c_dependencies
    assert "core_shipment.weight_kg" in air_e_dependencies
    assert "core_shipment.dimensions" in air_e_dependencies


def test_impacted_fetch_plan_items_preserves_order():
    request = _request(
        requested_mode=RequestedMode.sea,
        candidate_modes=[RequestedMode.sea],
    )
    plan = build_fetch_plan(request)

    impacted_items = impacted_fetch_plan_items(
        plan,
        ["cargo_flags.dangerous_goods"],
    )
    impacted_block_ids = [item.block_id for item in impacted_items]

    assert impacted_block_ids == [
        item.block_id
        for item in plan.items
        if item.block_id in impacted_block_ids
    ]
    assert impacted_block_ids.index("SEA-A") < impacted_block_ids.index("SEA-B")


def test_summarize_rerun_scope_for_road_cost_change():
    request = _request(
        requested_mode=RequestedMode.road,
        candidate_modes=[RequestedMode.road],
    )
    plan = build_fetch_plan(request)

    summary = summarize_rerun_scope(plan, ["commercial.incoterm"])

    assert summary["rerun_required"] is True
    assert "ROAD-F" in summary["impacted_planned_block_ids"]
    assert "ROAD-COST" in summary["impacted_planned_block_ids"]


def test_summarize_rerun_scope_unknown_field_no_rerun():
    request = _request(
        requested_mode=RequestedMode.road,
        candidate_modes=[RequestedMode.road],
    )
    plan = build_fetch_plan(request)

    summary = summarize_rerun_scope(plan, ["random.field"])

    assert summary["rerun_required"] is False
    assert summary["impacted_planned_block_ids"] == []
