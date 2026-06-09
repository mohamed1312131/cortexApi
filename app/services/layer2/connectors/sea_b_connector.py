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
from app.services.layer2.data_catalog import get_main_asset
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "SEA-B"
DEFAULT_DATA_PATH = Path("data/sea/sea_b_container_fit_rules.json")
CONTAINER_SPECS_PATH = Path("data/sea/sea_b_container_specs.json")
READINESS_RULES_PATH = Path("data/sea/sea_b_readiness_rules.json")
CARGO_EQUIPMENT_MAPPING_PATH = Path(
    "data/sea/sea_b_cargo_type_equipment_mapping.json"
)
CONFIDENCE_RULES_PATH = Path("data/sea/sea_b_confidence_rules.json")
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
    (
        "SEA-B is a planning fit reference only; equipment availability and "
        "stuffing/loading plan must be validated with forwarder/carrier."
    ),
    (
        "Container fit depends on exact piece dimensions, packaging, weight "
        "distribution, and carrier/equipment availability."
    ),
]


def _data_path() -> Path:
    asset = get_main_asset(BLOCK_ID)
    if asset is not None:
        return Path(asset.path)
    return DEFAULT_DATA_PATH


@lru_cache(maxsize=8)
def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _load_main_rules() -> list[dict[str, Any]]:
    return _safe_records(_load_json(_data_path()))


def _load_container_specs() -> list[dict[str, Any]]:
    return _safe_records(_load_json(CONTAINER_SPECS_PATH))


def _load_readiness_rules() -> list[dict[str, Any]]:
    return _safe_records(_load_json(READINESS_RULES_PATH))


def _load_cargo_equipment_mapping() -> list[dict[str, Any]]:
    return _safe_records(_load_json(CARGO_EQUIPMENT_MAPPING_PATH))


def _load_confidence_rules() -> list[dict[str, Any]]:
    return _safe_records(_load_json(CONFIDENCE_RULES_PATH))


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
    active: list[str] = []
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


def _dimensions_to_cm(dimensions: list[float] | None) -> list[float] | None:
    if dimensions is None:
        return None

    converted = [float(value) for value in dimensions]
    if converted and max(converted) < 20:
        return [round(value * 100.0, 3) for value in converted]
    return converted


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


def fetch_sea_b(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    shipment = request.core_shipment
    weight_kg = shipment.weight_kg
    volume_cbm = shipment.volume_cbm
    dimensions = shipment.dimensions
    provenance = provenance_for(BLOCK_ID, source)

    if weight_kg is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            missing_fields=["core_shipment.weight_kg"],
            unknowns=[
                Unknown(
                    field="core_shipment.weight_kg",
                    reason="shipment weight missing",
                    impact="Container/load fit cannot be checked.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    if volume_cbm is None and dimensions is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            data={"weight_kg": weight_kg},
            missing_fields=[
                "core_shipment.volume_cbm",
                "core_shipment.dimensions",
            ],
            unknowns=[
                Unknown(
                    field="core_shipment.volume_cbm",
                    reason="shipment volume/dimensions missing",
                    impact="Container fit cannot be estimated.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    main_rules = _load_main_rules()
    container_specs = _load_container_specs()
    readiness_rules = _load_readiness_rules()
    mapping_records = _load_cargo_equipment_mapping()
    _load_confidence_rules()

    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)
    matched_readiness_rules = _matched_readiness_rules(
        readiness_rules,
        active_flags,
    )

    unknowns, missing_fields = _request_unknowns(
        request=request,
        unknown_flags=unknown_flags,
        matched_readiness_rules=matched_readiness_rules,
        main_rules=main_rules,
        container_specs=container_specs,
    )
    planning_factors = _planning_factors(active_flags)
    hard_gates, hard_gate_unknowns = _hard_gate_findings(
        [*main_rules, *matched_readiness_rules]
    )
    unknowns.extend(hard_gate_unknowns)

    confidence = BlockConfidence(
        source_confidence=(
            SourceConfidence.planning_reference
            if main_rules or container_specs
            else SourceConfidence.unknown
        )
    )
    if volume_cbm is None or dimensions is None:
        confidence.cap = 0.5
        confidence.reasons.append(
            "SEA-B fit is capped by missing volume or dimensions."
        )

    candidate_specs = container_specs or main_rules
    data = {
        "fit_status": "planning_only_requires_forwarder_carrier_validation",
        "weight_kg": weight_kg,
        "volume_cbm": volume_cbm,
        "dimensions_cm": _dimensions_to_cm(dimensions),
        "quantity": shipment.quantity,
        "packaging": shipment.packaging,
        "active_trigger_flags": active_flags,
        "unknown_trigger_flags": unknown_flags,
        "container_fit_rule_count": len(main_rules),
        "container_spec_count": len(container_specs),
        "candidate_container_examples": [
            _container_example(record) for record in candidate_specs[:5]
        ],
        "matched_readiness_rule_ids": [
            str(rule.get("rule_id"))
            for rule in matched_readiness_rules
            if rule.get("rule_id")
        ],
        "cargo_equipment_mapping_examples": [
            _mapping_example(record) for record in mapping_records[:5]
        ],
    }

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.sea,
        status=(
            BlockStatus.found
            if hard_gates
            else BlockStatus.unknown
            if unknowns
            else BlockStatus.found
        ),
        data=data,
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=confidence,
        provenance=provenance,
    )


def _matched_readiness_rules(
    readiness_rules: list[dict[str, Any]],
    active_flags: list[str],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in readiness_rules:
        triggers = _normalized_tokens(rule.get("trigger_flags"))
        applies_to = _normalized_tokens(rule.get("applies_to"))
        rule_scope = triggers | applies_to

        if "any_sea_shipment" in rule_scope:
            matched.append(rule)
            continue
        if set(active_flags) & rule_scope:
            matched.append(rule)

    return matched


def _normalized_tokens(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value.strip().lower()} if value.strip() else set()
    if isinstance(value, list):
        return {
            str(item).strip().lower()
            for item in value
            if str(item).strip()
        }
    return set()


def _request_unknowns(
    *,
    request: ValidatedShipmentRequest,
    unknown_flags: list[str],
    matched_readiness_rules: list[dict[str, Any]],
    main_rules: list[dict[str, Any]],
    container_specs: list[dict[str, Any]],
) -> tuple[list[Unknown], list[str]]:
    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Sea container/load fit requirements may be incomplete.",
        )
        for flag in unknown_flags
    ]
    missing_fields: list[str] = []
    shipment = request.core_shipment

    if shipment.volume_cbm is None:
        missing_fields.append("core_shipment.volume_cbm")
        unknowns.append(
            Unknown(
                field="core_shipment.volume_cbm",
                reason="shipment volume missing",
                impact="Container utilization planning cannot be confirmed.",
            )
        )

    if shipment.dimensions is None:
        missing_fields.append("core_shipment.dimensions")
        unknowns.append(
            Unknown(
                field="core_shipment.dimensions",
                reason="shipment dimensions missing",
                impact="Piece/container loading fit cannot be confirmed.",
            )
        )

    if not main_rules:
        unknowns.append(
            Unknown(
                field="sea_b.container_fit_rules",
                reason="SEA-B container fit rules dataset is empty or malformed",
                impact="Container/load fit reference cannot be checked; do not treat as clear.",
            )
        )

    if not container_specs:
        unknowns.append(
            Unknown(
                field="sea_b.container_specs",
                reason="SEA-B container specs dataset is empty or malformed",
                impact="Candidate container examples cannot be checked; do not treat as clear.",
            )
        )

    if not matched_readiness_rules:
        unknowns.append(
            Unknown(
                field="sea_b.readiness_rules",
                reason="no SEA-B readiness rule matched this request",
                impact=(
                    "Container/load readiness cannot be fully verified; do not "
                    "treat as clear."
                ),
            )
        )

    if request.cargo_flags.oversized in {FlagState.yes, FlagState.likely}:
        unknowns.append(
            Unknown(
                field="cargo_flags.oversized",
                reason="oversized cargo requires equipment-specific validation",
                impact=(
                    "Special container, flat rack, open-top, breakbulk, or "
                    "engineering validation may be required."
                ),
            )
        )

    return unknowns, missing_fields


def _hard_gate_findings(
    records: list[dict[str, Any]],
) -> tuple[list[HardGate], list[Unknown]]:
    gates: list[HardGate] = []
    unknowns: list[Unknown] = []
    for record in records:
        hard_gate = record.get("hard_gate")
        if hard_gate is True:
            basis = _record_basis(record)
            gates.append(
                HardGate(
                    gate_id=f"SEA_B_{_gate_token(basis)}_HARD_GATE",
                    mode=RequestedMode.sea,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        record.get("hard_gate_reason")
                        or "SEA-B fit/readiness rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=basis,
                )
            )
        elif "hard_gate" in record and not isinstance(hard_gate, bool):
            unknowns.append(
                Unknown(
                    field="sea_b.hard_gate",
                    reason="SEA-B rule has malformed hard_gate",
                    impact="Fit/readiness rule cannot be treated as clear.",
                )
            )
    return gates, unknowns


def _record_basis(record: dict[str, Any]) -> str:
    for key in (
        "rule_id",
        "category",
        "equipment_code",
        "equipment_name",
        "cargo_type",
        "factor_type",
    ):
        value = record.get(key)
        if value:
            return str(value)
    return "RULE"


def _gate_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return token.upper() if token else "RULE"


def _container_example(record: dict[str, Any]) -> dict[str, Any]:
    example = {
        "equipment_code": record.get("equipment_code"),
        "equipment_name": record.get("equipment_name"),
        "container_type": record.get("container_type") or record.get("family"),
        "length_ft": record.get("length_ft"),
        "payload_kg": record.get("payload_kg") or record.get("reference_payload_kg"),
        "max_gross_weight_kg": (
            record.get("max_gross_weight_kg") or record.get("max_gross_kg")
        ),
        "internal_length_cm": _mm_to_cm(
            record.get("internal_length_cm") or record.get("internal_length_mm")
        ),
        "internal_width_cm": _mm_to_cm(
            record.get("internal_width_cm") or record.get("internal_width_mm")
        ),
        "internal_height_cm": _mm_to_cm(
            record.get("internal_height_cm") or record.get("internal_height_mm")
        ),
        "volume_cbm": record.get("volume_cbm") or record.get("capacity_cbm"),
        "notes": record.get("notes") or record.get("source_note"),
        "confidence": (
            record.get("confidence")
            or record.get("_confidence")
            or _source_confidence(None).value
        ),
    }
    return {key: value for key, value in example.items() if value is not None}


def _mapping_example(record: dict[str, Any]) -> dict[str, Any]:
    example = {
        "cargo_type": record.get("cargo_type"),
        "recommended_equipment": record.get("recommended_equipment"),
        "planning_factors": record.get("planning_factors"),
        "confidence": record.get("confidence") or record.get("_confidence"),
    }
    return {key: value for key, value in example.items() if value is not None}


def _mm_to_cm(value: Any) -> Any:
    if not isinstance(value, (int, float)):
        return value
    if value > 1000:
        return round(value / 10.0, 3)
    return value


def _planning_factors(active_flags: list[str]) -> list[str]:
    factors = list(_BASE_PLANNING_FACTORS)
    if {"temperature_controlled", "pharma", "food_perishable"} & set(active_flags):
        factors.append(
            "Reefer, temperature-controlled, or perishable cargo requires "
            "equipment availability validation."
        )
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods container planning requires DG stowage, "
            "segregation, and carrier validation."
        )
    return factors
