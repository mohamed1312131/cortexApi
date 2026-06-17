from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    GateSeverity,
    GateStatus,
    HardGate,
    RequestedMode,
    SourceConfidence,
    Unknown,
)
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "ROAD-C"
_DATA_PATH = Path("data/road/road_c_corridor_viability.json")


def _source_confidence(raw: str | SourceConfidence | None) -> SourceConfidence:
    if isinstance(raw, SourceConfidence):
        return raw
    if isinstance(raw, str):
        try:
            return SourceConfidence(raw.strip().lower())
        except ValueError:
            return SourceConfidence.unknown
    return SourceConfidence.unknown


@lru_cache(maxsize=1)
def _load_corridors() -> list[dict[str, Any]]:
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _lookup(
    corridors: list[dict[str, Any]],
    origin: str,
    destination: str,
) -> dict[str, Any] | None:
    for row in corridors:
        if (
            row.get("origin_country") == origin
            and row.get("destination_country") == destination
        ):
            return row
    return None


def fetch_road_c(
    origin_country: str | None,
    destination_country: str | None,
) -> BlockResponse:
    # ROAD-C data decides corridor gating. This connector only surfaces the authored decision.
    # Missing or malformed data is UNKNOWN, never clear.
    provenance = provenance_for(BLOCK_ID, str(_DATA_PATH))

    if not origin_country or not destination_country:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.skipped,
            missing_fields=["lane.origin_country", "lane.destination_country"],
            unknowns=[
                Unknown(
                    field="road_corridor_viability",
                    reason="origin or destination country not resolved",
                    impact="Road corridor viability cannot be checked.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["origin/destination country missing"],
            ),
            provenance=provenance,
        )

    record_id = f"{origin_country}->{destination_country}"
    corridors = _load_corridors()
    row = _lookup(corridors, origin_country, destination_country)

    if row is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="road_corridor_viability",
                    reason=f"no ROAD-C corridor record for {record_id}",
                    impact=(
                        "Road corridor viability is unverified for this lane; "
                        "do not treat as clear."
                    ),
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["no corridor record found for this country pair"],
            ),
            provenance=provenance_for(BLOCK_ID, str(_DATA_PATH), record_id),
        )

    confidence = BlockConfidence(
        source_confidence=_source_confidence(row.get("_confidence")),
    )
    provenance = provenance_for(BLOCK_ID, str(_DATA_PATH), record_id)

    hard_gate = row.get("hard_gate")
    if not isinstance(hard_gate, bool):
        confidence.reasons.append("ROAD-C record missing or malformed hard_gate field")
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            data=row,
            unknowns=[
                Unknown(
                    field="road_corridor_viability.hard_gate",
                    reason=(
                        f"ROAD-C record {record_id} has missing or malformed hard_gate"
                    ),
                    impact="Road corridor viability cannot be treated as clear.",
                )
            ],
            confidence=confidence,
            provenance=provenance,
        )

    if "confidence_cap_if_missing" in row:
        cap = row.get("confidence_cap_if_missing")
        try:
            # ROAD-C stores cap as 0-100; BlockConfidence.cap stores normalized 0-1.
            confidence.cap = float(cap) / 100.0
            confidence.reasons.append(
                "confidence capped if corridor requirements unconfirmed "
                f"(rule {row.get('rule_id')})"
            )
        except (TypeError, ValueError):
            confidence.reasons.append(f"invalid confidence_cap_if_missing value: {cap}")

    if hard_gate is False:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.found,
            data=row,
            hard_gates=[],
            confidence=confidence,
            provenance=provenance,
        )

    gate = HardGate(
        gate_id=row.get("rule_id", "ROAD_C_HARD_GATE"),
        mode=RequestedMode.road,
        severity=GateSeverity.blocking,
        status=GateStatus.triggered,
        message=(
            row.get("transit_countries_note")
            or row.get("permit_type")
            or "Road corridor not viable as a normal road move."
        ),
        source_block=BLOCK_ID,
        basis=row.get("corridor_viability"),
    )
    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.road,
        status=BlockStatus.found,
        data=row,
        hard_gates=[gate],
        confidence=confidence,
        provenance=provenance,
    )
