from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    FlagState,
    GateSeverity,
    GateStatus,
    HardGate,
    RequestedMode,
    SourceConfidence,
    Unknown,
    ValidatedShipmentRequest,
)
from app.services.layer2.data_catalog import get_main_asset
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "AIR-D"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_d_dataset.json")
_TRIGGER_FLAGS = (
    "dangerous_goods",
    "temperature_controlled",
    "oversized",
    "high_value",
    "pharma",
    "food_perishable",
    "live_animals",
)
_BASE_PLANNING_FACTOR = (
    "Carrier capability is planning-reference only until airline/forwarder "
    "confirms acceptance."
)


def _data_path() -> Path:
    asset = get_main_asset(BLOCK_ID)
    if asset is not None:
        return Path(asset.path)
    return DEFAULT_DATA_PATH


@lru_cache(maxsize=1)
def _load_dataset() -> dict[str, Any]:
    try:
        with _data_path().open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(payload, dict):
        return payload
    return {}


def _source_confidence(raw: str | SourceConfidence | None) -> SourceConfidence:
    if isinstance(raw, SourceConfidence):
        return raw

    value = str(raw).strip().lower() if raw is not None else ""
    if value == "high":
        return SourceConfidence.verified
    if value == "medium":
        return SourceConfidence.estimated
    if value in {"low", "unknown"}:
        return SourceConfidence.unknown

    try:
        return SourceConfidence(value)
    except ValueError:
        return SourceConfidence.unknown


def _active_trigger_flags(request: ValidatedShipmentRequest) -> list[str]:
    active = ["any_air_shipment"]
    for flag in _TRIGGER_FLAGS:
        if getattr(request.cargo_flags, flag) in {FlagState.yes, FlagState.likely}:
            active.append(flag)
    return active


def _unknown_trigger_flags(request: ValidatedShipmentRequest) -> list[str]:
    unknowns: list[str] = []
    for flag in _TRIGGER_FLAGS:
        if getattr(request.cargo_flags, flag) == FlagState.unknown:
            unknowns.append(flag)
    return unknowns


def _boolish_yes(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False

    normalized = str(value).strip().lower() if value is not None else ""
    if normalized in {"yes", "true", "available", "supported"}:
        return True
    if normalized in {"no", "false", "not_available", "unsupported"}:
        return False
    return None


def fetch_air_d(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    dataset = _load_dataset()
    carrier_capabilities = _record_list(dataset, "carrier_capabilities")
    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)
    carrier_examples = [
        _carrier_example(record) for record in carrier_capabilities[:5]
    ]
    hard_gates = _hard_gates(carrier_capabilities)

    unknowns: list[Unknown] = []
    if not carrier_capabilities:
        unknowns.append(
            Unknown(
                field="air_d.carrier_capabilities",
                reason="AIR-D carrier capability dataset is empty",
                impact="Carrier capability cannot be checked; do not treat as clear.",
            )
        )

    unknowns.extend(
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Carrier capability requirements may be incomplete.",
        )
        for flag in unknown_flags
    )

    if "dangerous_goods" in active_flags and not _any_clear_yes(
        carrier_examples,
        ["dangerous_goods_acceptance"],
    ):
        unknowns.append(
            Unknown(
                field="carrier_capabilities.dangerous_goods_acceptance",
                reason=(
                    "no reference carrier capability clearly verifies DG "
                    "acceptance"
                ),
                impact="DG air movement requires airline validation.",
            )
        )

    if {"pharma", "temperature_controlled"} & set(active_flags) and not _any_clear_yes(
        carrier_examples,
        ["pharma_capability", "temperature_control_capability"],
    ):
        unknowns.append(
            Unknown(
                field="carrier_capabilities.temperature_pharma",
                reason=(
                    "no reference carrier capability clearly verifies "
                    "pharma/temperature capability"
                ),
                impact="Temperature/pharma air movement requires airline validation.",
            )
        )

    if "oversized" in active_flags and not _any_clear_yes(
        carrier_examples,
        [
            "oversized_cargo_capability",
            "freighter_capability",
            "main_deck_capability",
        ],
    ):
        unknowns.append(
            Unknown(
                field="carrier_capabilities.oversized",
                reason=(
                    "oversized cargo requires freighter/main-deck carrier "
                    "validation"
                ),
                impact="Oversized air cargo cannot be treated as clear.",
            )
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=(
            BlockStatus.found
            if hard_gates
            else BlockStatus.unknown
            if unknowns
            else BlockStatus.found
        ),
        data={
            "carrier_capability_status": (
                "planning_reference_requires_carrier_validation"
            ),
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "reference_carrier_count": len(carrier_capabilities),
            "carrier_examples": carrier_examples,
        },
        hard_gates=hard_gates,
        planning_factors=_planning_factors(active_flags),
        unknowns=unknowns,
        confidence=BlockConfidence(
            source_confidence=(
                SourceConfidence.planning_reference
                if carrier_capabilities
                else SourceConfidence.unknown
            ),
        ),
        provenance=provenance_for(BLOCK_ID, source),
    )


def _record_list(dataset: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = dataset.get(key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _carrier_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "carrier_code": record.get("carrier_code"),
        "carrier_name": record.get("carrier_name"),
        "dangerous_goods_acceptance": _first_present(
            record,
            "dangerous_goods_acceptance",
            "accepts_dangerous_goods",
        ),
        "pharma_capability": _first_present(
            record,
            "pharma_capability",
            "accepts_pharma",
        ),
        "temperature_control_capability": _first_present(
            record,
            "temperature_control_capability",
            "accepts_temperature_controlled",
        ),
        "live_animals_capability": _first_present(
            record,
            "live_animals_capability",
            "accepts_live_animals",
        ),
        "valuable_cargo_capability": _first_present(
            record,
            "valuable_cargo_capability",
            "accepts_valuable_cargo",
        ),
        "oversized_cargo_capability": _first_present(
            record,
            "oversized_cargo_capability",
            "accepts_heavy_oversized",
        ),
        "freighter_capability": _first_present(
            record,
            "freighter_capability",
            "has_freighter_network",
        ),
        "main_deck_capability": _first_present(
            record,
            "main_deck_capability",
            "has_freighter_network",
        ),
        "notes": record.get("notes") or record.get("known_restrictions"),
    }


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _any_clear_yes(records: list[dict[str, Any]], fields: list[str]) -> bool:
    return any(
        _boolish_yes(record.get(field)) is True
        for record in records
        for field in fields
    )


def _hard_gates(records: list[dict[str, Any]]) -> list[HardGate]:
    gates: list[HardGate] = []
    for record in records:
        if record.get("hard_gate") is not True:
            continue
        carrier = record.get("carrier_code") or record.get("carrier_name") or "UNKNOWN"
        gates.append(
            HardGate(
                gate_id=f"AIR_D_{_gate_token(str(carrier))}_HARD_GATE",
                mode=RequestedMode.air,
                severity=GateSeverity.blocking,
                status=GateStatus.triggered,
                message=(
                    record.get("hard_gate_reason")
                    or "Carrier capability record contains a hard gate."
                ),
                source_block=BLOCK_ID,
                basis=record.get("carrier_code") or record.get("carrier_name"),
            )
        )
    return gates


def _planning_factors(active_flags: list[str]) -> list[str]:
    factors = [_BASE_PLANNING_FACTOR]
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods acceptance varies by airline and must be confirmed before booking."
        )
    if {"temperature_controlled", "pharma", "food_perishable"} & set(active_flags):
        factors.append(
            "Temperature-controlled service requires carrier product and station validation."
        )
    if "oversized" in active_flags:
        factors.append(
            "Oversized cargo may require freighter/main-deck carrier validation."
        )
    return factors


def _gate_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
