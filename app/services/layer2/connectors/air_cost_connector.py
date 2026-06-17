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

BLOCK_ID = "AIR-COST"
DEFAULT_DATA_PATH = Path("data/air/air_reference.json")

# Region buckets present in air_reference.rate_reference_usd_per_kg.
_ASIA = {
    "CN", "HK", "TW", "JP", "KR", "SG", "MY", "TH", "VN", "ID", "PH", "IN",
    "BD", "LK", "KH", "MM", "PK", "MO",
}
_EUROPE = {
    "FR", "DE", "IT", "ES", "NL", "BE", "LU", "GB", "AT", "PL", "CZ", "HU",
    "RO", "PT", "IE", "DK", "SE", "NO", "FI", "CH", "GR", "SK", "BG", "HR",
}
_NORTH_AMERICA = {"US", "CA", "MX"}
_MIDDLE_EAST = {"AE", "QA", "SA", "KW", "OM", "BH", "IL", "JO", "LB", "TR"}

# Volumetric density threshold (kg/m3) — also in air_reference.chargeable_weight.
_DEFAULT_VOLUMETRIC_DENSITY = 167.0

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
    "AIR-COST is a planning reference only, not a quote or rate confirmation.",
    (
        "Actual air freight depends on live carrier rates, fuel/security/DG "
        "surcharges, capacity, lane and season; confirm with carrier/forwarder."
    ),
]


def _data_path() -> Path:
    asset = get_main_asset(BLOCK_ID)
    if asset is not None:
        return Path(asset.path)
    return DEFAULT_DATA_PATH


@lru_cache(maxsize=1)
def _load_reference() -> dict[str, Any]:
    try:
        with _data_path().open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _region(country: str | None) -> str | None:
    if not country:
        return None
    code = country.strip().upper()
    if code in _ASIA:
        return "asia"
    if code in _EUROPE:
        return "europe"
    if code in _NORTH_AMERICA:
        return "north_america"
    if code in _MIDDLE_EAST:
        return "middle_east"
    return None


def _rate_bucket(origin_country: str | None, destination_country: str | None) -> str:
    o = _region(origin_country)
    d = _region(destination_country)
    if o == "asia" and d == "europe":
        return "asia_to_europe"
    if o == "asia" and d == "north_america":
        return "asia_to_north_america"
    if o == "asia" and d == "middle_east":
        return "asia_to_middle_east"
    return "general_default"


def _volumetric_kg(
    request: ValidatedShipmentRequest,
    density: float,
) -> tuple[float | None, str | None]:
    """Volumetric (dimensional) weight in kg, plus the basis used.

    Prefers explicit volume_cbm; falls back to a bounding-box volume from
    dimensions (metres) when volume is absent. Returns (kg, basis) or (None, None).
    """
    shipment = request.core_shipment
    if shipment.volume_cbm and shipment.volume_cbm > 0:
        return round(shipment.volume_cbm * density, 1), "volume_cbm"
    dims = shipment.dimensions
    if dims and len(dims) == 3 and all(d and d > 0 for d in dims):
        volume_m3 = dims[0] * dims[1] * dims[2]
        return round(volume_m3 * density, 1), "dimensions_bounding_box"
    return None, None


def _round_band(value: float) -> int:
    return int(round(value))


def fetch_air_cost(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    provenance = provenance_for(BLOCK_ID, source)
    reference = _load_reference()

    origin_country = request.lane.origin_country
    destination_country = request.lane.destination_country
    shipment = request.core_shipment

    active_flags = [
        flag
        for flag in _TRIGGER_FLAGS
        if getattr(request.cargo_flags, flag) in {FlagState.yes, FlagState.likely}
    ]
    unknown_flags = [
        flag
        for flag in _TRIGGER_FLAGS
        if getattr(request.cargo_flags, flag) == FlagState.unknown
    ]

    chargeable_block = reference.get("chargeable_weight", {})
    density = chargeable_block.get("volumetric_density_threshold_kg_per_m3") or _DEFAULT_VOLUMETRIC_DENSITY

    actual_kg = shipment.weight_kg if shipment.weight_kg and shipment.weight_kg > 0 else None
    volumetric_kg, volumetric_basis = _volumetric_kg(request, float(density))

    # chargeable weight = max(actual, volumetric) per IATA rule
    if actual_kg is not None and volumetric_kg is not None:
        chargeable_kg = max(actual_kg, volumetric_kg)
        chargeable_basis = "max(actual, volumetric)"
    elif actual_kg is not None:
        chargeable_kg = actual_kg
        chargeable_basis = "actual_only_volumetric_unknown"
    elif volumetric_kg is not None:
        chargeable_kg = volumetric_kg
        chargeable_basis = "volumetric_only_actual_unknown"
    else:
        chargeable_kg = None
        chargeable_basis = None

    bucket = _rate_bucket(origin_country, destination_country)
    rates = reference.get("rate_reference_usd_per_kg", {}).get(bucket) or {}
    surcharges = reference.get("surcharges_pct_of_base", {})
    transit = reference.get("transit_days_door_to_door", {}).get(bucket)

    planning_factors = list(_BASE_PLANNING_FACTORS)
    unknowns: list[Unknown] = []
    missing_fields: list[str] = []

    # surcharge fractions (planning): fuel + security + DG when applicable
    fuel = surcharges.get("fuel", {})
    security = surcharges.get("security", {})
    dg = surcharges.get("dangerous_goods", {})
    dg_active = "dangerous_goods" in active_flags
    sec_typ = security.get("typical", 0.0)
    sur_low = fuel.get("low", 0.0) + sec_typ + (dg.get("low", 0.0) if dg_active else 0.0)
    sur_typ = fuel.get("typical", 0.0) + sec_typ + (
        (dg.get("low", 0.0) + dg.get("high", 0.0)) / 2 if dg_active else 0.0
    )
    sur_high = fuel.get("high", 0.0) + sec_typ + (dg.get("high", 0.0) if dg_active else 0.0)

    estimated_cost_usd: dict[str, int] | None = None
    if chargeable_kg is not None and rates:
        estimated_cost_usd = {
            "low": _round_band(chargeable_kg * rates.get("low", 0.0) * (1 + sur_low)),
            "typical": _round_band(chargeable_kg * rates.get("typical", 0.0) * (1 + sur_typ)),
            "high": _round_band(chargeable_kg * rates.get("high", 0.0) * (1 + sur_high)),
        }

    # readiness gaps that cap the estimate
    if actual_kg is None:
        missing_fields.append("core_shipment.weight_kg")
        unknowns.append(
            Unknown(
                field="core_shipment.weight_kg",
                reason="shipment weight missing",
                impact="Air chargeable weight and cost cannot be estimated.",
            )
        )
    if volumetric_kg is None:
        unknowns.append(
            Unknown(
                field="core_shipment.volume_cbm",
                reason="volume/dimensions missing",
                impact=(
                    "Volumetric (dimensional) weight cannot be computed; air cost "
                    "may be underestimated for low-density cargo."
                ),
            )
        )
    if request.commercial.incoterm is None:
        unknowns.append(
            Unknown(
                field="commercial.incoterm",
                reason="incoterm missing",
                impact="Cost responsibility split cannot be confirmed.",
            )
        )
    if bucket == "general_default":
        planning_factors.append(
            "Lane did not match a specific rate corridor; general default air rate "
            "range used — confidence is lower."
        )
    if dg_active:
        planning_factors.append(
            "Dangerous-goods surcharge applied to the planning estimate; actual DG "
            "surcharge varies by carrier and UN number."
        )
    if volumetric_basis == "dimensions_bounding_box":
        planning_factors.append(
            "Volumetric weight derived from a bounding-box volume; palletized/irregular "
            "cargo may chargeable-weigh more."
        )

    data = {
        "cost_status": "planning_reference_not_a_quote",
        "currency": "USD",
        "origin_country": origin_country,
        "destination_country": destination_country,
        "rate_basis": bucket,
        "actual_weight_kg": actual_kg,
        "volumetric_weight_kg": volumetric_kg,
        "volumetric_basis": volumetric_basis,
        "chargeable_weight_kg": chargeable_kg,
        "chargeable_weight_basis": chargeable_basis,
        "rate_usd_per_kg": rates or None,
        "surcharges_applied_pct_of_base": {
            "fuel": fuel or None,
            "security": sec_typ,
            "dangerous_goods": dg if dg_active else None,
        },
        "estimated_cost_usd": estimated_cost_usd,
        "transit_days_door_to_door": transit,
        "active_trigger_flags": ["any_air_shipment", *active_flags],
        "unknown_trigger_flags": unknown_flags,
    }

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=BlockStatus.unknown,
        data=data,
        planning_factors=planning_factors,
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.planning_reference,
            cap=0.5,
            reasons=[
                "AIR-COST is a planning estimate; live carrier quote required for "
                "quote-grade pricing."
            ],
        ),
        provenance=provenance,
    )
