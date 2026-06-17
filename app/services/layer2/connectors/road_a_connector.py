from __future__ import annotations

import json
import re
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

BLOCK_ID = "ROAD-A"
_DATA_PATH = Path("data/road/road_a_dg_road_acceptance.json")
_UN_RE = re.compile(r"^UN\d{4}$")

# road_acceptance_status values that mean the substance is forbidden on road.
_PROHIBITED_STATUSES = {"prohibited_or_not_accepted"}


def _normalize_un(value: object) -> str | None:
    if not value:
        return None
    token = str(value).upper().replace(" ", "")
    return token if _UN_RE.fullmatch(token) else None


def _source_confidence(raw: object) -> SourceConfidence:
    if isinstance(raw, SourceConfidence):
        return raw
    if isinstance(raw, str):
        try:
            return SourceConfidence(raw.strip().lower())
        except ValueError:
            return SourceConfidence.unknown
    return SourceConfidence.unknown


@lru_cache(maxsize=1)
def _load_records() -> list[dict[str, Any]]:
    try:
        with _DATA_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _lookup(records: list[dict[str, Any]], un_number: str) -> dict[str, Any] | None:
    # Duplicate UN numbers are allowed in ADR (different entries). We surface the
    # first match; ambiguous/entry-dependent rows are already marked
    # road_acceptance_status="check_required" in the data, so the worker is told
    # the specific substance is still needed.
    for row in records:
        if _normalize_un(row.get("identification_number")) == un_number:
            return row
    return None


def fetch_road_a(request: ValidatedShipmentRequest) -> BlockResponse:
    provenance = provenance_for(BLOCK_ID, "request_profiles")
    dangerous_goods = request.cargo_flags.dangerous_goods

    if dangerous_goods == FlagState.no:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
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
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="cargo_flags.dangerous_goods",
                    reason="dangerous goods status is unknown",
                    impact="Road ADR requirements cannot be confirmed.",
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
    un_number = _normalize_un(profile.get("un_number"))

    if not un_number:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            data={"dangerous_goods": "yes_or_likely"},
            missing_fields=["profiles.dangerous_goods.un_number"],
            unknowns=[
                Unknown(
                    field="profiles.dangerous_goods.un_number",
                    reason="UN number missing for dangerous goods cargo",
                    impact="ADR classification and acceptance cannot be confirmed.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    record = _lookup(_load_records(), un_number)

    # UN number present but not in our ADR reference yet: degrade gracefully to the
    # prior behavior (specialist validation) rather than treating it as clear.
    if record is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            data={
                "dangerous_goods": "yes_or_likely",
                "un_number": un_number,
                "adr_check": "requires_specialist_validation",
            },
            unknowns=[
                Unknown(
                    field="road_dg_acceptance",
                    reason=f"no ROAD-A ADR record for {un_number}",
                    impact="ADR road acceptance is unverified for this UN number; do not treat as clear.",
                )
            ],
            planning_factors=[
                "ADR requirements must be validated by carrier/specialist before booking."
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
                reasons=["no ADR record found for this UN number"],
            ),
            provenance=provenance_for(BLOCK_ID, str(_DATA_PATH), un_number),
        )

    return _build_found_response(record, un_number)


def _build_found_response(record: dict[str, Any], un_number: str) -> BlockResponse:
    provenance = provenance_for(BLOCK_ID, str(_DATA_PATH), un_number)
    status_value = record.get("road_acceptance_status")
    confidence = BlockConfidence(source_confidence=_source_confidence(record.get("_confidence")))

    data = {
        "dangerous_goods": "yes_or_likely",
        "un_number": un_number,
        "identification_number": record.get("identification_number"),
        "proper_shipping_name": record.get("proper_shipping_name"),
        "hazard_class": record.get("hazard_class"),
        "packing_group": record.get("packing_group"),
        "adr_tunnel_code": record.get("adr_tunnel_code"),
        "tunnel_code_meaning": record.get("tunnel_code_meaning"),
        "limited_quantity": record.get("limited_quantity"),
        "road_acceptance_status": status_value,
    }

    hard_gates: list[HardGate] = []
    planning_factors: list[str] = [
        "ADR compliance (documentation, driver ADR certificate, vehicle marking) "
        "must be validated by carrier/specialist before booking."
    ]
    unknowns: list[Unknown] = []

    is_prohibited = record.get("hard_gate") is True or status_value in _PROHIBITED_STATUSES
    if is_prohibited:
        hard_gates.append(
            HardGate(
                gate_id="ROAD_A_DG_HARD_GATE",
                mode=RequestedMode.road,
                severity=GateSeverity.blocking,
                status=GateStatus.triggered,
                message="Road DG (ADR) acceptance record contains a hard gate.",
                source_block=BLOCK_ID,
                basis=str(status_value) if status_value else None,
            )
        )

    tunnel_code = record.get("adr_tunnel_code")
    if tunnel_code:
        planning_factors.append(
            f"ADR tunnel restriction {tunnel_code}: "
            f"{record.get('tunnel_code_meaning') or 'see ADR tunnel category rules'}"
        )

    # Ambiguous / entry-dependent goods (n.o.s., multi-entry): the UN number alone
    # is not enough; the worker must supply the specific substance/concentration.
    if status_value == "check_required":
        unknowns.append(
            Unknown(
                field="profiles.dangerous_goods.substance_detail",
                reason=(
                    f"{un_number} has multiple ADR entries; packing group / tunnel "
                    "code depend on the specific substance or concentration"
                ),
                impact="Exact ADR classification cannot be confirmed from the UN number alone.",
            )
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.road,
        status=BlockStatus.found,
        data=data,
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        confidence=confidence,
        provenance=provenance,
    )
