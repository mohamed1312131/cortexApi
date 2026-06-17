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

BLOCK_ID = "SEA-A"
DEFAULT_DATA_PATH = Path("data/sea/sea_a_dg_sea_acceptance.json")
_DATA_FIELDS = [
    "dg_key",
    "identification_number",
    "proper_shipping_name",
    "hazard_class_or_division",
    "packing_group",
    "label_codes",
    "vessel_stowage_category",
    "stowage_location",
    "cargo_vessel_stowage",
    "passenger_vessel_stowage",
    "passenger_vessel_restriction",
    "vessel_stowage_provision_codes",
    "segregation_notes",
    "imdg_segregation_group",
    "sea_acceptance_status",
    "hard_gate",
    "hard_gate_triggers",
]
_PLANNING_FACTORS = [
    "Sea DG acceptance must be validated with carrier/forwarder before booking."
]


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
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict):
        return []
    records = payload.get("records", [])
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


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


def fetch_sea_a(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    provenance = provenance_for(BLOCK_ID, source)
    dangerous_goods = request.cargo_flags.dangerous_goods

    if dangerous_goods == FlagState.no:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.sea,
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
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="cargo_flags.dangerous_goods",
                    reason="dangerous goods status is unknown",
                    impact=(
                        "SEA-A cannot determine whether IMDG/DG acceptance is "
                        "required."
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
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            missing_fields=["profiles.dangerous_goods.un_number"],
            unknowns=[
                Unknown(
                    field="profiles.dangerous_goods.un_number",
                    reason="UN number missing for dangerous goods cargo",
                    impact="Sea DG acceptance cannot be checked.",
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
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="profiles.dangerous_goods.un_number",
                    reason=f"no SEA-A DG record found for {normalized_un}",
                    impact=(
                        "Sea DG acceptance cannot be verified; do not treat as clear."
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
    if "confidence_cap_if_missing" in record:
        cap = record.get("confidence_cap_if_missing")
        try:
            confidence.cap = float(cap) / 100.0
            confidence.reasons.append(
                "SEA-A confidence capped by DG acceptance record completeness"
            )
        except (TypeError, ValueError):
            confidence.reasons.append(
                f"invalid confidence_cap_if_missing value: {cap}"
            )

    provenance = provenance_for(BLOCK_ID, source, record.get("dg_key") or normalized_un)

    hard_gate = record.get("hard_gate")
    unknowns = _record_unknowns(record)
    if not isinstance(hard_gate, bool):
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            data=data,
            unknowns=[
                Unknown(
                    field="sea_a.hard_gate",
                    reason="SEA-A record has missing or malformed hard_gate",
                    impact="Sea DG acceptance cannot be treated as clear.",
                )
            ],
            planning_factors=_PLANNING_FACTORS,
            confidence=confidence,
            provenance=provenance,
        )

    hard_gates = []
    if hard_gate is True:
        hard_gates.append(
            HardGate(
                gate_id="SEA_A_DG_HARD_GATE",
                mode=RequestedMode.sea,
                severity=GateSeverity.blocking,
                status=GateStatus.triggered,
                message="Sea DG acceptance record contains a hard gate.",
                source_block=BLOCK_ID,
                basis=_hard_gate_basis(record.get("hard_gate_triggers")),
            )
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.sea,
        status=(
            BlockStatus.found
            if hard_gates or not unknowns
            else BlockStatus.unknown
        ),
        data=data,
        hard_gates=hard_gates,
        unknowns=unknowns,
        planning_factors=_PLANNING_FACTORS,
        confidence=confidence,
        provenance=provenance,
    )


def _record_unknowns(record: dict[str, Any]) -> list[Unknown]:
    unknowns: list[Unknown] = []
    if not record.get("sea_acceptance_status"):
        unknowns.append(
            Unknown(
                field="sea_acceptance_status",
                reason="SEA-A record missing sea acceptance status",
                impact="Sea DG acceptance requires specialist validation.",
            )
        )
    if not record.get("vessel_stowage_category"):
        unknowns.append(
            Unknown(
                field="vessel_stowage_category",
                reason="stowage category missing",
                impact="Vessel stowage requirements require specialist validation.",
            )
        )
    return unknowns


def _hard_gate_basis(raw_triggers: Any) -> str:
    if isinstance(raw_triggers, list):
        return ", ".join(str(trigger) for trigger in raw_triggers)
    return str(raw_triggers)
