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
    RequestedMode,
    SourceConfidence,
    Unknown,
    ValidatedShipmentRequest,
)
from app.services.layer2.data_catalog import get_main_asset
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "AIR-E"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_e_dataset.json")
_BASE_PLANNING_FACTORS = [
    (
        "Aircraft/ULD fit is planning-only until airline confirms aircraft type, "
        "ULD availability, and piece dimensions."
    ),
    "Door dimensions and ULD limits must be validated before booking.",
]


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


def fetch_air_e(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    shipment = request.core_shipment
    weight_kg = shipment.weight_kg
    volume_cbm = shipment.volume_cbm
    dimensions = shipment.dimensions

    if weight_kg is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            missing_fields=["core_shipment.weight_kg"],
            unknowns=[
                Unknown(
                    field="core_shipment.weight_kg",
                    reason="shipment weight missing",
                    impact="Aircraft/ULD fit cannot be checked.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance_for(BLOCK_ID, source),
        )

    if volume_cbm is None and dimensions is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            data={"weight_kg": weight_kg},
            missing_fields=[
                "core_shipment.volume_cbm",
                "core_shipment.dimensions",
            ],
            unknowns=[
                Unknown(
                    field="core_shipment.volume_cbm",
                    reason="shipment volume missing",
                    impact="ULD fit cannot be estimated.",
                ),
                Unknown(
                    field="core_shipment.dimensions",
                    reason="shipment dimensions missing",
                    impact="Door/ULD fit cannot be checked.",
                ),
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance_for(BLOCK_ID, source),
        )

    dataset = _load_dataset()
    aircraft_specs = _record_list(dataset, "aircraft_fit_specs")
    uld_specs = _record_list(dataset, "uld_specs")
    unknowns = _unknowns_for_request(request)
    unknowns.extend(_unknowns_for_reference_data(dataset))
    planning_factors = list(_BASE_PLANNING_FACTORS)

    if request.cargo_flags.oversized in {FlagState.yes, FlagState.likely}:
        unknowns.append(
            Unknown(
                field="cargo_flags.oversized",
                reason="oversized air cargo requires aircraft-specific validation",
                impact="Main-deck/freighter aircraft validation is required.",
            )
        )
        planning_factors.append(
            "Oversized cargo may require freighter/main-deck service."
        )

    data = {
        "weight_kg": weight_kg,
        "volume_cbm": volume_cbm,
        "dimensions": dimensions,
        "quantity": shipment.quantity,
        "packaging": shipment.packaging,
        "fit_assessment": "planning_only_requires_airline_forwarder_validation",
        "reference_aircraft_count": len(aircraft_specs),
        "reference_uld_count": len(uld_specs),
        "possible_uld_families": _distinct_uld_families(uld_specs),
    }
    if dimensions is not None:
        (
            data["max_piece_length_cm"],
            data["max_piece_width_cm"],
            data["max_piece_height_cm"],
        ) = _dimensions_cm(dimensions)

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=BlockStatus.unknown if unknowns else BlockStatus.found,
        data=data,
        planning_factors=planning_factors,
        unknowns=unknowns,
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.planning_reference,
        ),
        provenance=provenance_for(BLOCK_ID, source),
    )


def _record_list(dataset: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = dataset.get(key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _unknowns_for_request(request: ValidatedShipmentRequest) -> list[Unknown]:
    unknowns: list[Unknown] = []
    if request.core_shipment.volume_cbm is None:
        unknowns.append(
            Unknown(
                field="core_shipment.volume_cbm",
                reason="shipment volume missing",
                impact="ULD volume planning cannot be confirmed.",
            )
        )
    if request.core_shipment.dimensions is None:
        unknowns.append(
            Unknown(
                field="core_shipment.dimensions",
                reason="shipment dimensions missing",
                impact="Door/ULD fit cannot be confirmed.",
            )
        )
    return unknowns


def _unknowns_for_reference_data(dataset: dict[str, Any]) -> list[Unknown]:
    if isinstance(dataset.get("aircraft_fit_specs"), list) and isinstance(
        dataset.get("uld_specs"),
        list,
    ):
        return []
    return [
        Unknown(
            field="air_e.reference_data",
            reason="AIR-E reference dataset missing or malformed",
            impact="Aircraft/ULD fit cannot be treated as clear.",
        )
    ]


def _distinct_uld_families(uld_specs: list[dict[str, Any]]) -> list[str]:
    families: list[str] = []
    for spec in uld_specs:
        family = spec.get("uld_family")
        if isinstance(family, str) and family and family not in families:
            families.append(family)
    return families


def _dimensions_cm(dimensions: list[float]) -> tuple[float, float, float]:
    multiplier = 100 if max(dimensions) < 20 else 1
    length, width, height = dimensions
    return (
        round(length * multiplier, 2),
        round(width * multiplier, 2),
        round(height * multiplier, 2),
    )
