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

BLOCK_ID = "SEA-COST"
DEFAULT_DATA_PATH = Path("data/sea/sea_cost_reference.json")
_TRIGGER_FLAGS = (
    "dangerous_goods",
    "temperature_controlled",
    "oversized",
    "high_value",
    "pharma",
    "food_perishable",
    "live_animals",
)
_BASE_PLANNING_FACTORS = [
    "SEA-COST is a planning reference only, not a quote or rate confirmation.",
    (
        "Actual ocean freight, surcharges, local charges, detention/demurrage, "
        "and validity must be confirmed with forwarder/carrier."
    ),
]
_COUNTRY_REGION_HINTS = {
    "CN": {"far east", "asia", "global"},
    "HK": {"far east", "asia", "global"},
    "JP": {"far east", "asia", "global"},
    "KR": {"far east", "asia", "global"},
    "SG": {"far east", "asia", "global"},
    "FR": {"north europe", "mediterranean", "europe", "global"},
    "IT": {"mediterranean", "europe", "global"},
    "ES": {"mediterranean", "europe", "global"},
    "DE": {"north europe", "europe", "global"},
    "NL": {"north europe", "europe", "global"},
    "BE": {"north europe", "europe", "global"},
    "GB": {"north europe", "europe", "global"},
    "US": {"us west coast", "us east coast", "global"},
}


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


def fetch_sea_cost(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    dataset = _load_dataset()
    lane_benchmarks = _record_list(dataset, "lane_benchmarks")
    surcharge_reference = _record_list(dataset, "surcharge_reference")
    local_charge_examples = _record_list(dataset, "local_charge_examples")
    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)

    origin_country = request.lane.origin_country
    destination_country = request.lane.destination_country
    shipment = request.core_shipment
    commercial = request.commercial

    unknowns, missing_fields = _request_unknowns(request, unknown_flags)
    if not lane_benchmarks:
        unknowns.append(
            Unknown(
                field="sea_cost.lane_benchmarks",
                reason="SEA-COST lane benchmark dataset is empty",
                impact="Sea cost reference cannot be checked.",
            )
        )
    if not surcharge_reference:
        unknowns.append(
            Unknown(
                field="sea_cost.surcharge_reference",
                reason="SEA-COST surcharge reference dataset is empty",
                impact="Surcharge exposure cannot be checked.",
            )
        )

    matched_benchmarks = _matched_lane_benchmarks(
        lane_benchmarks,
        origin_country,
        destination_country,
    )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.sea,
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
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "lane_benchmark_examples": [
                _lane_benchmark_example(record) for record in matched_benchmarks[:5]
            ],
            "surcharge_examples": [
                _surcharge_example(record) for record in surcharge_reference[:5]
            ],
            "local_charge_examples": [
                _local_charge_example(record) for record in local_charge_examples[:5]
            ],
        },
        planning_factors=_planning_factors(active_flags),
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=BlockConfidence(
            source_confidence=(
                SourceConfidence.planning_reference
                if lane_benchmarks or surcharge_reference
                else SourceConfidence.unknown
            ),
        ),
        provenance=provenance_for(BLOCK_ID, source),
    )


def _request_unknowns(
    request: ValidatedShipmentRequest,
    unknown_flags: list[str],
) -> tuple[list[Unknown], list[str]]:
    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Sea cost planning may be incomplete.",
        )
        for flag in unknown_flags
    ]
    missing_fields: list[str] = []

    if not request.lane.origin_country:
        missing_fields.append("lane.origin_country")
        unknowns.append(
            Unknown(
                field="lane.origin_country",
                reason="origin country missing",
                impact="Sea cost reference cannot be matched to a lane.",
            )
        )
    if not request.lane.destination_country:
        missing_fields.append("lane.destination_country")
        unknowns.append(
            Unknown(
                field="lane.destination_country",
                reason="destination country missing",
                impact="Sea cost reference cannot be matched to a lane.",
            )
        )
    if request.core_shipment.weight_kg is None:
        missing_fields.append("core_shipment.weight_kg")
        unknowns.append(
            Unknown(
                field="core_shipment.weight_kg",
                reason="shipment weight missing",
                impact="Sea freight cost planning may be incomplete.",
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
                impact="Container/LCL cost planning cannot be estimated.",
            )
        )
    if not request.commercial.incoterm:
        missing_fields.append("commercial.incoterm")
        unknowns.append(
            Unknown(
                field="commercial.incoterm",
                reason="incoterm missing",
                impact="Cost responsibility split cannot be confirmed.",
            )
        )
    if not request.commercial.ready_date:
        missing_fields.append("commercial.ready_date")
        unknowns.append(
            Unknown(
                field="commercial.ready_date",
                reason="ready date missing",
                impact="Seasonality/surcharge timing cannot be checked.",
            )
        )
    return unknowns, missing_fields


def _record_list(dataset: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = dataset.get(key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _matched_lane_benchmarks(
    records: list[dict[str, Any]],
    origin_country: str | None,
    destination_country: str | None,
) -> list[dict[str, Any]]:
    if not records:
        return []

    origin_hints = _country_hints(origin_country)
    destination_hints = _country_hints(destination_country)
    matched: list[dict[str, Any]] = []
    for record in records:
        origin_text = _normalize(record.get("origin_region"))
        destination_text = _normalize(record.get("destination_region"))
        lane_text = _normalize(record.get("lane_family"))
        if (
            _has_hint(origin_text, lane_text, origin_hints)
            and _has_hint(destination_text, lane_text, destination_hints)
        ):
            matched.append(record)

    if matched:
        return matched
    return records[:3]


def _country_hints(country: str | None) -> set[str]:
    if not country:
        return {"global"}
    return _COUNTRY_REGION_HINTS.get(str(country).upper(), {"global"})


def _has_hint(region_text: str, lane_text: str, hints: set[str]) -> bool:
    if "global" in region_text or "global" in lane_text:
        return True
    return any(
        hint in region_text or hint.replace(" ", "_") in lane_text
        for hint in hints
    )


def _lane_benchmark_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "lane_family": record.get("lane_family"),
        "origin_region": record.get("origin_region"),
        "destination_region": record.get("destination_region"),
        "equipment_basis": record.get("equipment_basis"),
        "benchmark_low_usd": record.get("benchmark_low_usd"),
        "benchmark_high_usd": record.get("benchmark_high_usd"),
        "reference_value_usd": record.get("reference_value_usd"),
        "price_date": record.get("price_date"),
        "basis": record.get("basis"),
        "cost_type": record.get("cost_type"),
        "not_a_quote": record.get("not_a_quote"),
        "requires_live_quote": record.get("requires_live_quote"),
    }


def _surcharge_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "category": record.get("category"),
        "charge_name": record.get("charge_name"),
        "applies_to": record.get("applies_to"),
        "unit": record.get("unit"),
        "planning_range_low": record.get("planning_range_low"),
        "planning_range_high": record.get("planning_range_high"),
        "currency": record.get("currency"),
        "not_a_quote": record.get("not_a_quote"),
        "requires_live_quote": record.get("requires_live_quote"),
    }


def _local_charge_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "country": record.get("country"),
        "port_or_scope": record.get("port_or_scope"),
        "carrier_or_authority": record.get("carrier_or_authority"),
        "charge_code": record.get("charge_code"),
        "charge_name": record.get("charge_name"),
        "equipment": record.get("equipment"),
        "amount": record.get("amount"),
        "currency": record.get("currency"),
        "unit": record.get("unit"),
        "effective_date": record.get("effective_date"),
        "valid_for_quote": record.get("valid_for_quote"),
        "not_a_quote": record.get("not_a_quote"),
    }


def _planning_factors(active_flags: list[str]) -> list[str]:
    factors = list(_BASE_PLANNING_FACTORS)
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods may trigger DG surcharges and carrier acceptance constraints."
        )
    if {"temperature_controlled", "pharma", "food_perishable"} & set(
        active_flags
    ):
        factors.append(
            "Reefer/perishable cargo may require equipment availability and reefer surcharges."
        )
    if "oversized" in active_flags:
        factors.append(
            "Oversized cargo may require special equipment, breakbulk, or engineering review."
        )
    return factors


def _normalize(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""
