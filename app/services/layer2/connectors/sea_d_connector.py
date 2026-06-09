from __future__ import annotations

import json
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

BLOCK_ID = "SEA-D"
DEFAULT_DATA_PATH = Path("data/sea/sea_d_carrier_trade_lane_reference.json")
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
    "Sea carrier/trade lane capability is planning-reference only until "
    "carrier/forwarder confirms acceptance and space."
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
    except (FileNotFoundError, OSError, json.JSONDecodeError):
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
    active = ["any_sea_shipment"]
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


def fetch_sea_d(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    dataset = _load_dataset()
    carrier_profiles = _record_list(dataset, "carrier_profiles")
    trade_lane_families = _record_list(dataset, "trade_lane_families")
    readiness_rules = _record_list(dataset, "readiness_rules")
    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)

    unknowns: list[Unknown] = []
    missing_fields: list[str] = []

    if request.lane.origin_country is None:
        missing_fields.append("lane.origin_country")
        unknowns.append(
            Unknown(
                field="lane.origin_country",
                reason="origin country missing",
                impact="Sea carrier/trade lane reference cannot be checked.",
            )
        )
    if request.lane.destination_country is None:
        missing_fields.append("lane.destination_country")
        unknowns.append(
            Unknown(
                field="lane.destination_country",
                reason="destination country missing",
                impact="Sea carrier/trade lane reference cannot be checked.",
            )
        )
    if not carrier_profiles:
        unknowns.append(
            Unknown(
                field="sea_d.carrier_profiles",
                reason="SEA-D carrier profile dataset is empty",
                impact="Carrier capability cannot be checked; do not treat as clear.",
            )
        )
    if not trade_lane_families:
        unknowns.append(
            Unknown(
                field="sea_d.trade_lane_families",
                reason="SEA-D trade lane family dataset is empty",
                impact="Trade lane reference cannot be checked; do not treat as clear.",
            )
        )

    unknowns.extend(
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Sea carrier/trade lane requirements may be incomplete.",
        )
        for flag in unknown_flags
    )

    hard_gates = _hard_gates(
        [*carrier_profiles, *trade_lane_families, *readiness_rules]
    )
    planning_factors = _planning_factors(active_flags)

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.sea,
        status=(
            BlockStatus.found
            if hard_gates
            else BlockStatus.unknown
            if unknowns
            else BlockStatus.found
        ),
        data={
            "carrier_trade_lane_status": (
                "planning_reference_requires_carrier_forwarder_validation"
            ),
            "origin_country": request.lane.origin_country,
            "destination_country": request.lane.destination_country,
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "reference_carrier_count": len(carrier_profiles),
            "trade_lane_family_count": len(trade_lane_families),
            "carrier_examples": [
                _carrier_example(record) for record in carrier_profiles[:5]
            ],
            "trade_lane_examples": [
                _trade_lane_example(record) for record in trade_lane_families[:5]
            ],
        },
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=BlockConfidence(
            source_confidence=(
                SourceConfidence.planning_reference
                if carrier_profiles or trade_lane_families
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
        "carrier_type": record.get("carrier_type"),
        "headquarters_region": record.get("headquarters_region"),
        "public_network_reference": record.get("public_network_reference"),
        "public_schedule_lookup": record.get("public_schedule_lookup"),
        "public_booking_or_quote_portal": record.get(
            "public_booking_or_quote_portal"
        ),
        "main_trade_lane_families": record.get("main_trade_lane_families"),
        "service_capability_tags": record.get("service_capability_tags"),
        "verified_public_claims": record.get("verified_public_claims"),
    }


def _trade_lane_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_lane_code": record.get("trade_lane_code"),
        "trade_lane_name": record.get("trade_lane_name"),
        "typical_service_pattern": record.get("typical_service_pattern"),
        "typical_frequency_hint": record.get("typical_frequency_hint"),
        "planning_implication": record.get("planning_implication"),
    }


def _hard_gates(records: list[dict[str, Any]]) -> list[HardGate]:
    gates: list[HardGate] = []
    for record in records:
        if record.get("hard_gate") is not True:
            continue
        gates.append(
            HardGate(
                gate_id="SEA_D_HARD_GATE",
                mode=RequestedMode.sea,
                severity=GateSeverity.blocking,
                status=GateStatus.triggered,
                message=(
                    record.get("hard_gate_reason")
                    or "SEA-D record contains a hard gate."
                ),
                source_block=BLOCK_ID,
                basis=_record_basis(record),
            )
        )
    return gates


def _record_basis(record: dict[str, Any]) -> Any:
    for key in (
        "carrier_code",
        "trade_lane_code",
        "rule_id",
        "category",
        "factor_type",
    ):
        if record.get(key):
            return record.get(key)
    return record.get("carrier_name") or record.get("trade_lane_name")


def _planning_factors(active_flags: list[str]) -> list[str]:
    factors = [_BASE_PLANNING_FACTOR]
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods acceptance varies by carrier, route, port, and "
            "vessel; carrier validation is required."
        )
    if {"temperature_controlled", "pharma", "food_perishable"} & set(active_flags):
        factors.append(
            "Temperature-controlled or perishable cargo requires "
            "reefer/service validation."
        )
    if "oversized" in active_flags:
        factors.append(
            "Oversized sea cargo may require special equipment, breakbulk, or "
            "carrier engineering validation."
        )
    return factors
