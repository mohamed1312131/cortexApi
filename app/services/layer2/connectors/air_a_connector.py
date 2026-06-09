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

BLOCK_ID = "AIR-A"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_a_dg_records_REPAIRED.json")
_DATA_FIELDS = [
    "record_id",
    "symbols",
    "proper_shipping_name",
    "hazard_class_or_division",
    "identification_number",
    "packing_group",
    "label_codes",
    "passenger_aircraft_rail_limit_9A",
    "cargo_aircraft_only_limit_9B",
    "air_acceptance_status",
    "cargo_aircraft_only_required",
    "hard_gate",
]
_BASE_PLANNING_FACTOR = (
    "Air DG acceptance must be validated with airline/forwarder before booking."
)


def _data_path() -> Path:
    asset = get_main_asset(BLOCK_ID)
    if asset is not None:
        return Path(asset.path)
    return DEFAULT_DATA_PATH


@lru_cache(maxsize=1)
def _load_records() -> list[dict[str, Any]]:
    try:
        with _data_path().open(encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, list):
        return []
    return [record for record in payload if isinstance(record, dict)]


def _source_confidence(raw: str | SourceConfidence | None) -> SourceConfidence:
    if isinstance(raw, SourceConfidence):
        return raw
    value = str(raw).strip().lower() if raw is not None else ""
    try:
        return SourceConfidence(value)
    except ValueError:
        return SourceConfidence.unknown


def _normalize_un(value: Any) -> str:
    text = str(value).strip().upper().replace(" ", "") if value is not None else ""
    if text.isdigit():
        return f"UN{text}"
    if text.startswith("UN"):
        return f"UN{text[2:]}"
    return text


def _find_record(records: list[dict[str, Any]], un_number: str) -> dict[str, Any] | None:
    normalized_un = _normalize_un(un_number)
    for record in records:
        if _normalize_un(record.get("identification_number")) == normalized_un:
            return record
    return None


def fetch_air_a(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    provenance = provenance_for(BLOCK_ID, source)
    dangerous_goods = request.cargo_flags.dangerous_goods

    if dangerous_goods == FlagState.no:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.not_applicable,
            data={"dangerous_goods": "no"},
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.authored,
            ),
            provenance=provenance,
        )

    if dangerous_goods == FlagState.unknown:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="cargo_flags.dangerous_goods",
                    reason="dangerous goods status is unknown",
                    impact=(
                        "AIR-A cannot determine whether IATA/air DG acceptance "
                        "is required."
                    ),
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    profile = request.profiles.get("dangerous_goods", {})
    if not isinstance(profile, dict):
        profile = {}
    un_number = profile.get("un_number")

    if not un_number:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            missing_fields=["profiles.dangerous_goods.un_number"],
            unknowns=[
                Unknown(
                    field="profiles.dangerous_goods.un_number",
                    reason="UN number missing for dangerous goods cargo",
                    impact="Air DG acceptance cannot be checked.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    normalized_un = _normalize_un(un_number)
    record = _find_record(_load_records(), normalized_un)
    if record is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="profiles.dangerous_goods.un_number",
                    reason=f"no AIR-A DG record found for {normalized_un}",
                    impact=(
                        "Air DG acceptance cannot be verified; do not treat as clear."
                    ),
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance_for(BLOCK_ID, source, normalized_un),
        )

    data = {field: record.get(field) for field in _DATA_FIELDS}
    confidence = BlockConfidence(
        source_confidence=_source_confidence(record.get("_confidence")),
    )
    provenance = provenance_for(BLOCK_ID, source, record.get("record_id") or normalized_un)
    planning_factors = _planning_factors(record)

    hard_gate = record.get("hard_gate")
    unknowns = _record_unknowns(record)
    if not isinstance(hard_gate, bool):
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            data=data,
            unknowns=[
                Unknown(
                    field="air_a.hard_gate",
                    reason="AIR-A record has missing or malformed hard_gate",
                    impact="Air DG acceptance cannot be treated as clear.",
                ),
                *unknowns,
            ],
            planning_factors=planning_factors,
            confidence=confidence,
            provenance=provenance,
        )

    hard_gates = []
    if hard_gate is True:
        hard_gates.append(
            HardGate(
                gate_id="AIR_A_DG_HARD_GATE",
                mode=RequestedMode.air,
                severity=GateSeverity.blocking,
                status=GateStatus.triggered,
                message="Air DG acceptance record contains a hard gate.",
                source_block=BLOCK_ID,
                basis=record.get("air_acceptance_status"),
            )
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=(
            BlockStatus.found
            if hard_gates or not unknowns
            else BlockStatus.unknown
        ),
        data=data,
        hard_gates=hard_gates,
        unknowns=unknowns,
        planning_factors=planning_factors,
        confidence=confidence,
        provenance=provenance,
    )


def _record_unknowns(record: dict[str, Any]) -> list[Unknown]:
    if record.get("air_acceptance_status"):
        return []
    return [
        Unknown(
            field="air_acceptance_status",
            reason="AIR-A record missing air acceptance status",
            impact="Air DG acceptance requires specialist validation.",
        )
    ]


def _planning_factors(record: dict[str, Any]) -> list[str]:
    factors = [_BASE_PLANNING_FACTOR]
    if record.get("cargo_aircraft_only_required") is True:
        factors.insert(
            0,
            (
                "Cargo Aircraft Only restriction may apply; passenger/belly air "
                "option requires carrier validation."
            ),
        )
    return factors
