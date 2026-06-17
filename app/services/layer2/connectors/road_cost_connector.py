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

BLOCK_ID = "ROAD-COST"
DEFAULT_DATA_PATH = Path("data/road/road_cost_reference.json")
_TRIGGER_FLAGS = (
    "dangerous_goods",
    "temperature_controlled",
    "oversized",
    "high_value",
    "pharma",
    "food_perishable",
    "live_animals",
)
_COUNTRY_REGION_HINTS = {
    "FR": {"europe", "western", "central", "eu", "global"},
    "IT": {"europe", "western", "central", "eu", "global"},
    "DE": {"europe", "western", "central", "eu", "global"},
    "ES": {"europe", "western", "central", "eu", "global"},
    "NL": {"europe", "western", "central", "eu", "global"},
    "BE": {"europe", "western", "central", "eu", "global"},
    "LU": {"europe", "western", "central", "eu", "global"},
    "AT": {"europe", "western", "central", "eu", "global"},
    "PL": {"europe", "eastern", "south-eastern", "eu", "global"},
    "CZ": {"europe", "eastern", "central", "eu", "global"},
    "HU": {"europe", "eastern", "south-eastern", "eu", "global"},
    "RO": {"europe", "eastern", "south-eastern", "eu", "global"},
    "GB": {"uk", "eu", "europe", "global"},
    "MA": {"north africa", "africa", "global"},
    "TN": {"north africa", "africa", "global"},
    "DZ": {"north africa", "africa", "global"},
}
_BASE_PLANNING_FACTORS = [
    "ROAD-COST is a planning reference only, not a quote or rate confirmation.",
    (
        "Actual road freight, surcharges, tolls, border delays, waiting time, "
        "permits, and validity must be confirmed with carrier/forwarder."
    ),
]


def _data_path() -> Path:
    asset = get_main_asset(BLOCK_ID)
    if asset is not None:
        return Path(asset.path)
    return DEFAULT_DATA_PATH


@lru_cache(maxsize=1)
def _load_dataset() -> Any:
    try:
        with _data_path().open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_records(payload: Any) -> list[dict[str, Any]]:
    records: Any = []
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("records"), list):
            records = payload["records"]
        else:
            for value in payload.values():
                if isinstance(value, list):
                    records = value
                    break

    return [record for record in records if isinstance(record, dict)]


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
    active = ["any_road_shipment"]
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


def fetch_road_cost(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    cost_records = _safe_records(_load_dataset())
    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)
    origin_country = request.lane.origin_country
    destination_country = request.lane.destination_country
    shipment = request.core_shipment
    commercial = request.commercial
    matched_records = _matched_cost_records(
        cost_records,
        origin_country,
        destination_country,
    )
    selected_records = matched_records or cost_records[:5]

    unknowns, missing_fields = _request_unknowns(
        request=request,
        unknown_flags=unknown_flags,
        cost_records=cost_records,
        matched_records=matched_records,
    )
    confidence = BlockConfidence(
        source_confidence=(
            SourceConfidence.planning_reference
            if cost_records
            else SourceConfidence.unknown
        )
    )
    if (
        origin_country is None
        or destination_country is None
        or shipment.weight_kg is None
        or commercial.incoterm is None
        or commercial.ready_date is None
    ):
        confidence.cap = 0.5
        confidence.reasons.append(
            "ROAD-COST confidence capped by missing lane/commercial/cargo planning fields."
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.road,
        status=BlockStatus.unknown if unknowns else BlockStatus.found,
        data={
            "cost_status": "planning_reference_not_a_quote",
            "origin_country": origin_country,
            "destination_country": destination_country,
            "origin_city": request.lane.origin_city,
            "destination_city": request.lane.destination_city,
            "weight_kg": shipment.weight_kg,
            "volume_cbm": shipment.volume_cbm,
            "incoterm": commercial.incoterm,
            "ready_date": commercial.ready_date,
            "deadline": commercial.deadline,
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "cost_reference_count": len(cost_records),
            "cost_reference_examples": [
                _cost_example(record) for record in selected_records[:5]
            ],
        },
        planning_factors=_planning_factors(active_flags),
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=confidence,
        provenance=provenance_for(BLOCK_ID, source),
    )


def _matched_cost_records(
    records: list[dict[str, Any]],
    origin_country: str | None,
    destination_country: str | None,
) -> list[dict[str, Any]]:
    if origin_country is None or destination_country is None:
        return []

    pair = f"{origin_country}->{destination_country}".lower()
    reverse_pair = f"{destination_country}->{origin_country}".lower()
    origin_hints = _COUNTRY_REGION_HINTS.get(origin_country, {"global"})
    destination_hints = _COUNTRY_REGION_HINTS.get(destination_country, {"global"})
    lane_hints = {
        hint.lower()
        for hint in origin_hints | destination_hints
        if hint.lower() != "global"
    }

    matched: list[dict[str, Any]] = []
    for record in records:
        text = _record_match_text(record)
        if pair in text or reverse_pair in text:
            matched.append(record)
            continue
        if origin_country.lower() in text and destination_country.lower() in text:
            matched.append(record)
            continue
        if lane_hints and any(hint in text for hint in lane_hints):
            matched.append(record)

    return matched


def _record_match_text(record: dict[str, Any]) -> str:
    keys = (
        "origin_country",
        "destination_country",
        "origin_region",
        "destination_region",
        "region_or_scenario",
        "corridor",
        "lane",
        "lane_family",
        "country_pair",
        "applies_to",
    )
    return " ".join(str(record.get(key) or "") for key in keys).lower()


def _request_unknowns(
    *,
    request: ValidatedShipmentRequest,
    unknown_flags: list[str],
    cost_records: list[dict[str, Any]],
    matched_records: list[dict[str, Any]],
) -> tuple[list[Unknown], list[str]]:
    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Road cost planning may be incomplete.",
        )
        for flag in unknown_flags
    ]
    missing_fields: list[str] = []

    if request.lane.origin_country is None:
        missing_fields.append("lane.origin_country")
        unknowns.append(
            Unknown(
                field="lane.origin_country",
                reason="origin country missing",
                impact="Road cost reference cannot be matched to a lane.",
            )
        )
    if request.lane.destination_country is None:
        missing_fields.append("lane.destination_country")
        unknowns.append(
            Unknown(
                field="lane.destination_country",
                reason="destination country missing",
                impact="Road cost reference cannot be matched to a lane.",
            )
        )
    if request.core_shipment.weight_kg is None:
        missing_fields.append("core_shipment.weight_kg")
        unknowns.append(
            Unknown(
                field="core_shipment.weight_kg",
                reason="shipment weight missing",
                impact="Road cost planning may be incomplete.",
            )
        )
    if (
        request.core_shipment.volume_cbm is None
        and request.core_shipment.dimensions is None
    ):
        missing_fields.extend(
            ["core_shipment.volume_cbm", "core_shipment.dimensions"]
        )
        unknowns.append(
            Unknown(
                field="core_shipment.volume_cbm",
                reason="shipment volume/dimensions missing",
                impact="Road vehicle/cost planning cannot be estimated.",
            )
        )
    if request.commercial.incoterm is None:
        missing_fields.append("commercial.incoterm")
        unknowns.append(
            Unknown(
                field="commercial.incoterm",
                reason="incoterm missing",
                impact="Cost responsibility split cannot be confirmed.",
            )
        )
    if request.commercial.ready_date is None:
        missing_fields.append("commercial.ready_date")
        unknowns.append(
            Unknown(
                field="commercial.ready_date",
                reason="ready date missing",
                impact="Timing-related road cost factors cannot be checked.",
            )
        )
    if not cost_records:
        unknowns.append(
            Unknown(
                field="road_cost.reference",
                reason="ROAD-COST reference dataset is empty",
                impact="Road cost planning reference cannot be checked.",
            )
        )
    if cost_records and not matched_records:
        unknowns.append(
            Unknown(
                field="road_cost.lane_match",
                reason="no lane-specific ROAD-COST reference matched this request",
                impact=(
                    "Only general planning examples are available; do not "
                    "treat as a quote."
                ),
            )
        )

    return unknowns, missing_fields


def _cost_example(record: dict[str, Any]) -> dict[str, Any]:
    cost_range = None
    if any(record.get(key) is not None for key in ("low", "typical", "high")):
        cost_range = {
            key: record.get(key)
            for key in ("low", "typical", "high")
            if record.get(key) is not None
        }

    unit = record.get("unit")
    example = {
        "lane": record.get("lane"),
        "lane_family": record.get("lane_family")
        or record.get("region_or_scenario"),
        "country_pair": record.get("country_pair"),
        "origin_region": record.get("origin_region"),
        "destination_region": record.get("destination_region"),
        "vehicle_type": record.get("vehicle_type"),
        "cost_basis": record.get("cost_basis") or record.get("cost_category"),
        "cost_range": record.get("cost_range") or cost_range,
        "currency": record.get("currency") or _currency_from_unit(unit),
        "unit": unit,
        "surcharge_type": record.get("surcharge_type"),
        "planning_notes": record.get("planning_notes")
        or record.get("planning_note"),
        "known_limitations": record.get("known_limitations"),
        "confidence": (
            record.get("confidence")
            or record.get("_confidence")
            or _source_confidence(None).value
        ),
    }
    return {key: value for key, value in example.items() if value is not None}


def _currency_from_unit(unit: Any) -> str | None:
    if not isinstance(unit, str):
        return None
    upper = unit.upper()
    for currency in ("EUR", "USD", "GBP"):
        if upper.startswith(currency) or f"{currency}_" in upper:
            return currency
    return None


def _planning_factors(active_flags: list[str]) -> list[str]:
    factors = list(_BASE_PLANNING_FACTORS)
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods may trigger ADR-related cost and carrier acceptance constraints."
        )
    if {"temperature_controlled", "pharma", "food_perishable"} & set(active_flags):
        factors.append(
            "Temperature-controlled road movement may require specialized equipment and higher cost."
        )
    if "oversized" in active_flags:
        factors.append(
            "Oversized road cargo may require permits, escort, route survey, and special equipment costs."
        )
    return factors
