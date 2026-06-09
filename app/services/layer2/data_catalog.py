from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DataAsset:
    path: str
    mode: str
    block_id: str | None
    role: str
    purpose: str
    top_type: str
    top_keys: list[str]
    list_key: str | None
    record_count: int | None
    first_record_keys: list[str]
    connector_status: str
    notes: list[str] = field(default_factory=list)


def infer_mode_from_path(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "road" in parts:
        return "road"
    if "sea" in parts:
        return "sea"
    if "air" in parts:
        return "air"
    return "unknown"


def infer_block_id_from_filename(path: Path) -> str | None:
    name = path.name.lower()
    exact = {
        "air_airports.json": "AIR-C",
        "air_carriers.json": "AIR-D",
        "air_reference.json": "AIR-REF",
    }
    if name in exact:
        return exact[name]

    prefix_map = [
        ("road_a_", "ROAD-A"),
        ("road_b_", "ROAD-B"),
        ("road_c_", "ROAD-C"),
        ("road_f_", "ROAD-F"),
        ("road_cost_", "ROAD-COST"),
        ("sea_a_", "SEA-A"),
        ("sea_b_", "SEA-B"),
        ("sea_c_", "SEA-C"),
        ("sea_d_", "SEA-D"),
        ("sea_f_", "SEA-F"),
        ("sea_i_", "SEA-I"),
        ("sea_cost_", "SEA-COST"),
        ("cortex_air_block_a_", "AIR-A"),
        ("cortex_air_block_b_", "AIR-B"),
        ("cortex_air_block_c_", "AIR-C"),
        ("cortex_air_block_d_", "AIR-D"),
        ("cortex_air_block_e_", "AIR-E"),
        ("cortex_air_block_f_", "AIR-F"),
        ("cortex_air_block_h_", "AIR-H"),
        ("cortex_air_block_i_", "AIR-I"),
    ]
    for prefix, block_id in prefix_map:
        if name.startswith(prefix):
            return block_id
    return None


def infer_role_from_filename(path: Path) -> str:
    name = path.name.lower()
    main_datasets = {
        "road_c_corridor_viability.json",
        "road_b_vehicle_fit_profiles.json",
        "road_f_document_requirements.json",
        "sea_a_dg_sea_acceptance.json",
        "sea_b_container_fit_rules.json",
        "sea_c_port_capability.json",
        "sea_d_carrier_trade_lane_reference.json",
        "sea_f_maritime_documents_border_gates.json",
        "sea_i_chokepoints_schedule_readiness.json",
        "sea_cost_reference.json",
        "road_cost_reference.json",
        "cortex_air_block_a_dg_records_repaired.json",
        "cortex_air_block_b_dataset.json",
        "cortex_air_block_c_dataset.json",
        "cortex_air_block_d_dataset.json",
        "cortex_air_block_e_dataset.json",
        "cortex_air_block_f_dataset.json",
        "cortex_air_block_h_dataset.json",
        "cortex_air_block_i_dataset.json",
    }
    if name in main_datasets:
        return "main"

    if name in {"air_reference.json", "air_airports.json", "air_carriers.json"}:
        return "support"
    if "metadata" in name:
        return "metadata"
    if "confidence" in name:
        return "confidence"
    if "coverage" in name:
        return "coverage"
    if "source_refs" in name or "source_inventory" in name:
        return "source_refs"

    rules_keywords = [
        "readiness_rules",
        "validation_rules",
        "fit_rules",
        "border_rules",
        "route_feasibility_rules",
        "route_risk_rules",
        "security_status_matrix",
        "jurisdiction_security_rules",
        "category_rules",
        "pair_generation_rules",
    ]
    if any(keyword in name for keyword in rules_keywords):
        return "rules"

    support_keywords = [
        "field_mapping",
        "schema",
        "stowage_category_mapping",
        "country_groups",
        "jurisdiction_map",
    ]
    if any(keyword in name for keyword in support_keywords):
        return "support"

    return "unknown"


def connector_status_for(block_id: str | None, path: Path) -> str:
    if block_id is None:
        return "not_planned"

    status_by_block = {
        "ROAD-C": "implemented",
        "ROAD-A": "partial",
        "ROAD-B": "implemented",
        "ROAD-F": "implemented",
        "ROAD-COST": "implemented",
        "SEA-C": "implemented",
        "SEA-B": "implemented",
        "SEA-F": "partial",
        "SEA-A": "implemented",
        "SEA-D": "implemented",
        "SEA-I": "implemented",
        "SEA-COST": "implemented",
        "AIR-A": "implemented",
        "AIR-B": "implemented",
        "AIR-C": "implemented",
        "AIR-D": "implemented",
        "AIR-E": "implemented",
        "AIR-F": "implemented",
        "AIR-H": "implemented",
        "AIR-I": "implemented",
        "AIR-REF": "planned",
    }
    return status_by_block.get(block_id, "planned")


def inspect_json_file(
    path: Path,
) -> tuple[str, list[str], str | None, int | None, list[str], list[str]]:
    notes: list[str] = []
    try:
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return "invalid", [], None, None, [], [f"json inspection failed: {exc}"]

    if isinstance(payload, dict):
        top_keys = list(payload.keys())[:30]
        list_key = None
        record_count = None
        first_record_keys: list[str] = []
        for key, value in payload.items():
            if isinstance(value, list):
                list_key = key
                record_count = len(value)
                if value and isinstance(value[0], dict):
                    first_record_keys = list(value[0].keys())[:80]
                break
        return "dict", top_keys, list_key, record_count, first_record_keys, notes

    if isinstance(payload, list):
        first_record_keys = []
        if payload and isinstance(payload[0], dict):
            first_record_keys = list(payload[0].keys())[:80]
        return "list", [], None, len(payload), first_record_keys, notes

    return "scalar", [], None, None, [], notes


def build_data_catalog(root: Path = Path("data")) -> list[DataAsset]:
    assets: list[DataAsset] = []
    for path in sorted(root.rglob("*.json")):
        if any(part.startswith(".") for part in path.parts):
            continue

        mode = infer_mode_from_path(path)
        block_id = infer_block_id_from_filename(path)
        role = infer_role_from_filename(path)
        (
            top_type,
            top_keys,
            list_key,
            record_count,
            first_record_keys,
            notes,
        ) = inspect_json_file(path)
        status = connector_status_for(block_id, path)
        assets.append(
            DataAsset(
                path=path.as_posix(),
                mode=mode,
                block_id=block_id,
                role=role,
                purpose=_purpose_for(path, block_id, role),
                top_type=top_type,
                top_keys=top_keys,
                list_key=list_key,
                record_count=record_count,
                first_record_keys=first_record_keys,
                connector_status=status,
                notes=notes + _notes_for(block_id, role, top_type),
            )
        )
    return assets


def find_assets(
    mode: str | None = None,
    block_id: str | None = None,
    role: str | None = None,
    connector_status: str | None = None,
) -> list[DataAsset]:
    assets = build_data_catalog()
    if mode is not None:
        assets = [asset for asset in assets if asset.mode == mode]
    if block_id is not None:
        assets = [asset for asset in assets if asset.block_id == block_id]
    if role is not None:
        assets = [asset for asset in assets if asset.role == role]
    if connector_status is not None:
        assets = [
            asset
            for asset in assets
            if asset.connector_status == connector_status
        ]
    return assets


def get_main_asset(block_id: str) -> DataAsset | None:
    for asset in find_assets(block_id=block_id, role="main"):
        return asset
    return None


def summarize_catalog() -> dict[str, Any]:
    assets = build_data_catalog()
    return {
        "counts_by_mode": dict(Counter(asset.mode for asset in assets)),
        "counts_by_block_id": dict(
            Counter(asset.block_id or "None" for asset in assets)
        ),
        "counts_by_role": dict(Counter(asset.role for asset in assets)),
        "counts_by_connector_status": dict(
            Counter(asset.connector_status for asset in assets)
        ),
        "invalid_json_count": sum(1 for asset in assets if asset.top_type == "invalid"),
    }


def _purpose_for(path: Path, block_id: str | None, role: str) -> str:
    if block_id is None:
        return f"Unmapped {role} data asset"
    return f"{role} data asset for {block_id}"


def _notes_for(block_id: str | None, role: str, top_type: str) -> list[str]:
    notes: list[str] = []
    if block_id is None:
        notes.append("no block_id inferred from filename")
    if role == "unknown":
        notes.append("role not inferred from filename")
    if top_type == "invalid":
        notes.append("invalid JSON; asset cannot be inspected")
    return notes
