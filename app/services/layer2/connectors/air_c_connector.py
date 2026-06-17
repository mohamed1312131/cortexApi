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

BLOCK_ID = "AIR-C"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_c_dataset.json")
_DATA_FIELDS = [
    "airport_code",
    "airport_name",
    "city_country",
    "cargo_terminal_available",
    "customs_available",
    "dangerous_goods_handling",
    "pharma_cold_chain",
    "temperature_controlled_storage",
    "frozen_storage",
    "perishable_handling",
    "live_animals_handling",
    "valuable_secure_storage",
    "heavy_oversized_handling",
    "uld_handling",
    "main_deck_freighter_handling",
    "security_screening",
    "known_handlers",
    "operating_hours_note",
]
_PLANNING_FACTOR = (
    "Airport capability must be validated with airline/handler before booking."
)
_COUNTRY_NAMES = {
    "AE": "United Arab Emirates",
    "CN": "China",
    "DE": "Germany",
    "FR": "France",
    "GB": "United Kingdom",
    "HK": "Hong Kong",
    "NL": "Netherlands",
    "QA": "Qatar",
    "SG": "Singapore",
    "TN": "Tunisia",
    "US": "United States",
}


def _data_path() -> Path:
    asset = get_main_asset(BLOCK_ID)
    if asset is not None:
        return Path(asset.path)
    return DEFAULT_DATA_PATH


@lru_cache(maxsize=1)
def _load_airports() -> list[dict[str, Any]]:
    try:
        with _data_path().open(encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict):
        return []
    airports = payload.get("airport_capabilities")
    if not isinstance(airports, list):
        return []
    return [airport for airport in airports if isinstance(airport, dict)]


def _source_confidence(raw: str | SourceConfidence | None) -> SourceConfidence:
    if isinstance(raw, SourceConfidence):
        return raw

    value = _normalize(raw)
    if value == "high":
        return SourceConfidence.verified
    if value == "medium":
        return SourceConfidence.estimated
    if value == "low":
        return SourceConfidence.unknown

    try:
        return SourceConfidence(value)
    except ValueError:
        return SourceConfidence.unknown


def _normalize(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _find_airport(
    airports: list[dict[str, Any]],
    city_or_airport: str | None,
    country_hint: str | None,
) -> dict[str, Any] | None:
    query = _normalize(city_or_airport)
    if not query:
        return None

    fields = ("airport_code", "airport_name", "city_country")
    exact_matches = [
        airport
        for airport in airports
        if any(_normalize(airport.get(field)) == query for field in fields)
    ]
    if exact_matches:
        return _prefer_country_match(exact_matches, country_hint)

    substring_matches = [
        airport
        for airport in airports
        if any(query in _normalize(airport.get(field)) for field in fields)
    ]
    if substring_matches:
        return _prefer_country_match(substring_matches, country_hint)

    return None


def fetch_air_c(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    origin_city = request.lane.origin_city
    origin_country = request.lane.origin_country

    if not origin_city:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.skipped,
            missing_fields=["lane.origin_city"],
            unknowns=[
                Unknown(
                    field="air_airport_capability",
                    reason="origin airport/city missing",
                    impact="Air airport capability cannot be checked.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["origin airport/city missing"],
            ),
            provenance=provenance_for(BLOCK_ID, source),
        )

    row = _find_airport(_load_airports(), origin_city, origin_country)
    record_id = f"{origin_city}->{origin_country}"
    if row is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="air_airport_capability",
                    reason=(
                        "no AIR-C airport capability record found for requested "
                        "origin"
                    ),
                    impact=(
                        "Air airport capability cannot be verified; do not treat "
                        "as clear."
                    ),
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["no AIR-C airport capability record found"],
            ),
            provenance=provenance_for(BLOCK_ID, source, record_id),
        )

    data = {field: row.get(field) for field in _DATA_FIELDS}
    unknowns = _capability_unknowns(request, row)
    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=BlockStatus.unknown if unknowns else BlockStatus.found,
        data=data,
        planning_factors=[_PLANNING_FACTOR],
        unknowns=unknowns,
        confidence=BlockConfidence(
            source_confidence=_source_confidence(row.get("confidence")),
        ),
        provenance=provenance_for(
            BLOCK_ID,
            source,
            row.get("airport_code") or row.get("airport_name"),
        ),
    )


def _prefer_country_match(
    airports: list[dict[str, Any]],
    country_hint: str | None,
) -> dict[str, Any]:
    country_tokens = _country_tokens(country_hint)
    if not country_tokens:
        return airports[0]

    for airport in airports:
        city_country = _normalize(airport.get("city_country"))
        if any(token in city_country for token in country_tokens):
            return airport
    return airports[0]


def _country_tokens(country_hint: str | None) -> list[str]:
    hint = str(country_hint).strip() if country_hint is not None else ""
    if not hint:
        return []

    tokens = [_normalize(hint)]
    country_name = _COUNTRY_NAMES.get(hint.upper())
    if country_name is not None:
        tokens.append(_normalize(country_name))
    return [token for token in tokens if token]


def _capability_unknowns(
    request: ValidatedShipmentRequest,
    row: dict[str, Any],
) -> list[Unknown]:
    unknowns: list[Unknown] = []

    if not _is_explicit_yes(row.get("cargo_terminal_available")):
        unknowns.append(
            Unknown(
                field="cargo_terminal_available",
                reason="cargo terminal availability is not verified as yes",
                impact="Air cargo handling requires airport/handler validation.",
            )
        )

    if not _is_explicit_yes(row.get("customs_available")):
        unknowns.append(
            Unknown(
                field="customs_available",
                reason="customs availability is not verified as yes",
                impact="Export/import airport readiness requires validation.",
            )
        )

    if (
        request.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}
        and not _is_explicit_yes(row.get("dangerous_goods_handling"))
    ):
        unknowns.append(
            Unknown(
                field="dangerous_goods_handling",
                reason="DG handling is not verified as yes at this airport",
                impact="Air DG handling requires airline/handler validation.",
            )
        )

    if (
        request.cargo_flags.temperature_controlled
        in {
            FlagState.yes,
            FlagState.likely,
        }
        and not _is_explicit_yes(row.get("temperature_controlled_storage"))
    ):
        unknowns.append(
            Unknown(
                field="temperature_controlled_storage",
                reason="temperature-controlled storage is not verified as yes",
                impact=(
                    "Temperature-controlled air cargo requires handler "
                    "validation."
                ),
            )
        )

    return unknowns


def _is_explicit_yes(value: Any) -> bool:
    if value is True:
        return True
    return _normalize(value) in {"yes", "true"}
