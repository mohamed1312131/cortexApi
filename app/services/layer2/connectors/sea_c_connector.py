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
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "SEA-C"
_DATA_PATH = Path("data/sea/sea_c_port_capability.json")
_DATA_FIELDS = [
    "port_key",
    "wpi_number",
    "unlocode",
    "main_port_name",
    "alternate_port_name",
    "country_iso2",
    "cap_draft_m",
    "cap_draft_basis",
    "cap_container",
    "cap_container_basis",
    "cap_dg_handling",
    "cap_dg_handling_basis",
    "cap_cranes",
    "cap_cranes_basis",
    "cap_roro",
    "cap_roro_basis",
    "cap_liquid_bulk",
    "cap_liquid_bulk_basis",
    "cap_solid_bulk",
    "cap_solid_bulk_basis",
    "known_hard_gate_constraints",
    "readiness_tags",
]


@lru_cache(maxsize=1)
def _load_ports() -> list[dict[str, Any]]:
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict):
        return []
    ports = payload.get("ports", [])
    if not isinstance(ports, list):
        return []
    return [row for row in ports if isinstance(row, dict)]


def _source_confidence(raw: str | SourceConfidence | None) -> SourceConfidence:
    if isinstance(raw, SourceConfidence):
        return raw
    match _normalize(raw):
        case "verified" | "verified_base":
            return SourceConfidence.verified
        case "authored":
            return SourceConfidence.authored
        case "estimated":
            return SourceConfidence.estimated
        case "planning_reference":
            return SourceConfidence.planning_reference
        case _:
            return SourceConfidence.unknown


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _find_port(
    ports: list[dict[str, Any]],
    city_or_port: str | None,
    country_iso2: str | None,
) -> dict[str, Any] | None:
    needle = _normalize(city_or_port)
    if not needle:
        return None

    country = _normalize(country_iso2)
    country_matches = [
        row for row in ports if country and _normalize(row.get("country_iso2")) == country
    ]
    search_groups = [country_matches, ports] if country_matches else [ports]

    for rows in search_groups:
        exact_match = _match_port_rows(rows, needle, exact=True)
        if exact_match is not None:
            return exact_match

    for rows in search_groups:
        substring_match = _match_port_rows(rows, needle, exact=False)
        if substring_match is not None:
            return substring_match

    return None


def _match_port_rows(
    rows: list[dict[str, Any]],
    needle: str,
    *,
    exact: bool,
) -> dict[str, Any] | None:
    for row in rows:
        values = [
            _normalize(row.get("main_port_name")),
            _normalize(row.get("alternate_port_name")),
            _normalize(row.get("unlocode")),
        ]
        if exact and needle in values:
            return row
        if not exact and any(needle in value for value in values if value):
            return row
    return None


def fetch_sea_c(request: ValidatedShipmentRequest) -> BlockResponse:
    origin_city = request.lane.origin_city
    origin_country = request.lane.origin_country
    provenance = provenance_for(BLOCK_ID, str(_DATA_PATH))

    if not origin_city:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.sea,
            status=BlockStatus.skipped,
            missing_fields=["lane.origin_city"],
            unknowns=[
                Unknown(
                    field="sea_port_capability",
                    reason="origin port/city missing",
                    impact="Sea port capability cannot be checked.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["origin port/city missing"],
            ),
            provenance=provenance,
        )

    row = _find_port(_load_ports(), origin_city, origin_country)
    record_id = f"{origin_city}->{origin_country}"
    if row is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="sea_port_capability",
                    reason="no SEA-C port capability record found for requested origin",
                    impact=(
                        "Sea port capability cannot be verified; do not treat as clear."
                    ),
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["no SEA-C port record found"],
            ),
            provenance=provenance_for(BLOCK_ID, str(_DATA_PATH), record_id),
        )

    data = {field: row.get(field) for field in _DATA_FIELDS}
    confidence = BlockConfidence(
        source_confidence=_source_confidence(row.get("_confidence")),
    )
    if "confidence_cap_if_missing" in row:
        cap = row.get("confidence_cap_if_missing")
        try:
            confidence.cap = float(cap) / 100.0
            confidence.reasons.append(
                "SEA-C confidence capped by port capability data completeness"
            )
        except (TypeError, ValueError):
            confidence.reasons.append(
                f"invalid confidence_cap_if_missing value: {cap}"
            )

    unknowns = _capability_unknowns(request, data)
    hard_gates = _hard_gates(data.get("known_hard_gate_constraints"))
    provenance = provenance_for(
        BLOCK_ID,
        str(_DATA_PATH),
        row.get("port_key") or row.get("unlocode") or row.get("main_port_name"),
    )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.sea,
        status=BlockStatus.unknown if unknowns else BlockStatus.found,
        data=data,
        hard_gates=hard_gates,
        unknowns=unknowns,
        confidence=confidence,
        provenance=provenance,
    )


def _capability_unknowns(
    request: ValidatedShipmentRequest,
    data: dict[str, Any],
) -> list[Unknown]:
    unknowns: list[Unknown] = []

    if _normalize(data.get("cap_container")) in {"", "unknown"}:
        unknowns.append(
            Unknown(
                field="cap_container",
                reason="container capability is unknown for this port",
                impact="Container sea option requires terminal validation.",
            )
        )

    if (
        request.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}
        and _normalize(data.get("cap_dg_handling")) != "yes"
    ):
        unknowns.append(
            Unknown(
                field="cap_dg_handling",
                reason="DG handling is not verified as yes for this port",
                    impact="Dangerous goods sea handling requires terminal validation.",
                )
            )

    if data.get("cap_draft_m") is None:
        unknowns.append(
            Unknown(
                field="cap_draft_m",
                reason="draft capability is missing",
                impact="Vessel/port draft suitability cannot be checked.",
            )
        )

    return unknowns


def _hard_gates(raw_constraints: Any) -> list[HardGate]:
    if not isinstance(raw_constraints, list):
        return []

    return [
        HardGate(
            gate_id=f"SEA_C_{str(constraint).upper()}",
            mode=RequestedMode.sea,
            severity=GateSeverity.high,
            status=GateStatus.triggered,
            message=str(constraint),
            source_block=BLOCK_ID,
            basis="known_hard_gate_constraints",
        )
        for constraint in raw_constraints
        if constraint
    ]
