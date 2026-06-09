from __future__ import annotations

from app.schemas import FetchPlan, FetchPlanItem


ALL_BLOCK_IDS = {
    "ROAD-C",
    "ROAD-A",
    "ROAD-B",
    "ROAD-F",
    "ROAD-COST",
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
}


FIELD_TO_BLOCKS: dict[str, set[str]] = {
    "lane.origin_country": {
        "ROAD-C",
        "ROAD-F",
        "ROAD-COST",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "lane.destination_country": {
        "ROAD-C",
        "ROAD-F",
        "ROAD-COST",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "lane.origin_city": {
        "SEA-C",
        "SEA-I",
        "SEA-COST",
        "AIR-C",
        "AIR-I",
        "ROAD-F",
        "ROAD-COST",
    },
    "lane.destination_city": {
        "SEA-C",
        "SEA-I",
        "SEA-COST",
        "AIR-C",
        "AIR-I",
        "ROAD-F",
        "ROAD-COST",
    },
    "core_shipment.weight_kg": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-B",
        "SEA-F",
        "SEA-COST",
        "AIR-E",
        "AIR-F",
        "AIR-I",
    },
    "core_shipment.volume_cbm": {
        "ROAD-B",
        "ROAD-COST",
        "SEA-B",
        "SEA-COST",
        "AIR-E",
    },
    "core_shipment.dimensions": {
        "ROAD-B",
        "ROAD-COST",
        "SEA-B",
        "SEA-COST",
        "AIR-E",
    },
    "core_shipment.cargo_description": {
        "ROAD-A",
        "SEA-A",
        "AIR-A",
        "AIR-H",
    },
    "cargo_flags.dangerous_goods": {
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-A",
        "SEA-B",
        "SEA-D",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
        "AIR-A",
        "AIR-B",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "cargo_flags.temperature_controlled": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-B",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-B",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "cargo_flags.oversized": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-B",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-B",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "cargo_flags.high_value": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-D",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
        "AIR-B",
        "AIR-D",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "cargo_flags.pharma": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-B",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-B",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "cargo_flags.food_perishable": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-B",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-B",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "cargo_flags.live_animals": {
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
        "SEA-D",
        "SEA-I",
        "SEA-COST",
        "AIR-B",
        "AIR-D",
        "AIR-F",
        "AIR-H",
        "AIR-I",
    },
    "commercial.incoterm": {
        "ROAD-F",
        "ROAD-COST",
        "SEA-F",
        "SEA-COST",
        "AIR-F",
    },
    "commercial.ready_date": {
        "ROAD-F",
        "ROAD-COST",
        "SEA-I",
        "SEA-COST",
        "AIR-I",
    },
    "commercial.deadline": {
        "ROAD-F",
        "SEA-I",
        "AIR-I",
    },
    "mode.requested_mode": set(ALL_BLOCK_IDS),
    "mode.candidate_modes": set(ALL_BLOCK_IDS),
}


def _build_block_to_fields() -> dict[str, set[str]]:
    block_to_fields: dict[str, set[str]] = {}
    for field, block_ids in FIELD_TO_BLOCKS.items():
        for block_id in block_ids:
            block_to_fields.setdefault(block_id, set()).add(field)
    return block_to_fields


BLOCK_TO_FIELDS = _build_block_to_fields()


def block_dependencies(block_id: str) -> set[str]:
    return set(BLOCK_TO_FIELDS.get(block_id, set()))


def impacted_blocks_for_changed_fields(changed_fields: list[str]) -> set[str]:
    impacted_block_ids: set[str] = set()
    for changed_field in changed_fields:
        impacted_block_ids.update(FIELD_TO_BLOCKS.get(changed_field, set()))
        for field, block_ids in FIELD_TO_BLOCKS.items():
            if field.startswith(f"{changed_field}."):
                impacted_block_ids.update(block_ids)
    return impacted_block_ids


def impacted_fetch_plan_items(
    fetch_plan: FetchPlan,
    changed_fields: list[str],
) -> list[FetchPlanItem]:
    impacted_block_ids = impacted_blocks_for_changed_fields(changed_fields)
    return [item for item in fetch_plan.items if item.block_id in impacted_block_ids]


def summarize_rerun_scope(
    fetch_plan: FetchPlan,
    changed_fields: list[str],
) -> dict:
    impacted_block_ids = impacted_blocks_for_changed_fields(changed_fields)
    impacted_planned_items = impacted_fetch_plan_items(fetch_plan, changed_fields)
    impacted_planned_block_ids = [item.block_id for item in impacted_planned_items]
    planned_block_ids = {item.block_id for item in fetch_plan.items}
    unplanned_impacted_block_ids = impacted_block_ids - planned_block_ids

    return {
        "changed_fields": list(changed_fields),
        "impacted_block_ids": sorted(impacted_block_ids),
        "impacted_planned_block_ids": impacted_planned_block_ids,
        "unplanned_impacted_block_ids": sorted(unplanned_impacted_block_ids),
        "rerun_required": bool(impacted_planned_block_ids),
    }
