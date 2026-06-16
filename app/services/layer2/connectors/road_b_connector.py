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

BLOCK_ID = "ROAD-B"
DEFAULT_DATA_PATH = Path("data/road/road_b_vehicle_fit_profiles.json")
STANDARD_LIMITS_PATH = Path("data/road/road_b_standard_limits.json")
ABNORMAL_LOAD_RULES_PATH = Path("data/road/road_b_abnormal_load_rules.json")
CONFIDENCE_RULES_PATH = Path("data/road/road_b_confidence_rules.json")
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
        "ROAD-B is a planning fit reference only; vehicle availability and "
        "load plan must be validated with carrier."
    ),
    (
        "Road vehicle fit depends on exact piece dimensions, packaging, "
        "axle/load distribution, equipment availability, and route restrictions."
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


def _load_vehicle_profiles() -> list[dict[str, Any]]:
    return _safe_records(_load_json(_data_path()))


def _load_standard_limits() -> list[dict[str, Any]]:
    return _safe_records(_load_json(STANDARD_LIMITS_PATH))


def _load_abnormal_load_rules() -> list[dict[str, Any]]:
    return _safe_records(_load_json(ABNORMAL_LOAD_RULES_PATH))


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


def _dimensions_to_m(dimensions: list[float] | None) -> list[float] | None:
    if dimensions is None:
        return None

    converted = [float(value) for value in dimensions]
    if converted and max(converted) > 20:
        return [round(value / 100.0, 3) for value in converted]
    return converted


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


def fetch_road_b(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    provenance = provenance_for(BLOCK_ID, source)
    shipment = request.core_shipment
    weight_kg = shipment.weight_kg
    volume_cbm = shipment.volume_cbm
    dimensions = shipment.dimensions

    if weight_kg is None:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            missing_fields=["core_shipment.weight_kg"],
            unknowns=[
                Unknown(
                    field="core_shipment.weight_kg",
                    reason="shipment weight missing",
                    impact="Road vehicle/load fit cannot be checked.",
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
            mode=RequestedMode.road,
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
                    impact="Road vehicle/load fit cannot be estimated.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    vehicle_profiles = _load_vehicle_profiles()
    standard_limits = _load_standard_limits()
    abnormal_rules = _load_abnormal_load_rules()
    _load_confidence_rules()

    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)
    matched_abnormal_rules = _matched_abnormal_rules(abnormal_rules, active_flags)
    matched_vehicle_profiles = _matched_vehicle_profiles(
        vehicle_profiles,
        active_flags,
        shipment.cargo_description,
    )

    unknowns, missing_fields = _request_unknowns(
        request=request,
        unknown_flags=unknown_flags,
        matched_abnormal_rules=matched_abnormal_rules,
        vehicle_profiles=vehicle_profiles,
        standard_limits=standard_limits,
        abnormal_rules=abnormal_rules,
    )
    planning_factors = _planning_factors(active_flags)
    hard_gates, hard_gate_unknowns = _hard_gate_findings(
        [*matched_abnormal_rules, *matched_vehicle_profiles]
    )
    unknowns.extend(hard_gate_unknowns)

    confidence = BlockConfidence(
        source_confidence=(
            SourceConfidence.planning_reference
            if vehicle_profiles or standard_limits
            else SourceConfidence.unknown
        )
    )
    if volume_cbm is None or dimensions is None:
        confidence.cap = 0.5
        confidence.reasons.append(
            "ROAD-B fit is capped by missing volume or dimensions."
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.road,
        status=(
            BlockStatus.found
            if hard_gates
            else BlockStatus.unknown
            if unknowns
            else BlockStatus.found
        ),
        data={
            "fit_status": "planning_only_requires_carrier_validation",
            "weight_kg": weight_kg,
            "volume_cbm": volume_cbm,
            "dimensions_m": _dimensions_to_m(dimensions),
            "quantity": shipment.quantity,
            "packaging": shipment.packaging,
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "vehicle_profile_count": len(vehicle_profiles),
            "standard_limit_count": len(standard_limits),
            "abnormal_load_rule_count": len(abnormal_rules),
            "candidate_vehicle_examples": [
                _vehicle_example(record) for record in vehicle_profiles[:5]
            ],
            "matched_abnormal_rule_ids": [
                str(rule.get("rule_id"))
                for rule in matched_abnormal_rules
                if rule.get("rule_id")
            ],
            "matched_vehicle_profile_ids": [
                str(profile.get("cargo_profile_id"))
                for profile in matched_vehicle_profiles
                if profile.get("cargo_profile_id")
            ],
        },
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=confidence,
        provenance=provenance,
    )


def _matched_abnormal_rules(
    abnormal_rules: list[dict[str, Any]],
    active_flags: list[str],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    active = set(active_flags)
    for rule in abnormal_rules:
        triggers = _normalized_tokens(rule.get("trigger_flags"))
        applies_to = _normalized_tokens(rule.get("applies_to"))
        scope = triggers | applies_to

        if "any_road_shipment" in scope:
            matched.append(rule)
            continue
        if active & scope:
            matched.append(rule)
            continue
        if "oversized" in scope and "oversized" in active:
            matched.append(rule)

    return matched


# Structured flag gating for flag-driven vehicle profiles: a profile listed
# here matches only when EVERY required cargo flag is yes/likely. Unknown
# flags never match — the gap is surfaced explicitly via unknown_trigger_flags
# instead of silently widening the match. Type-specific profiles inside a flag
# family (e.g. batteries_lithium, hazardous_waste) are deliberately NOT listed:
# a bare flag cannot identify them, so they stay description-matched.
_PROFILE_FLAG_REQUIREMENTS: dict[str, frozenset[str]] = {
    "food_ambient_packaged": frozenset({"food_perishable"}),
    "food_chilled": frozenset({"food_perishable"}),
    "food_frozen": frozenset({"food_perishable"}),
    "pharma_temperature_controlled": frozenset({"pharma", "temperature_controlled"}),
    "refrigerated_dg_or_chemical": frozenset({"dangerous_goods", "temperature_controlled"}),
    "fragile_high_value_electronics": frozenset({"high_value"}),
    "livestock": frozenset({"live_animals"}),
    "oversized_machine_width_over_2_55m": frozenset({"oversized"}),
    "oversized_machine_height_over_4m": frozenset({"oversized"}),
    "very_heavy_machine_over_40t_gvw": frozenset({"oversized"}),
    "long_industrial_beams": frozenset({"oversized"}),
    "wind_turbine_blade": frozenset({"oversized"}),
    "transformer_large_power": frozenset({"oversized"}),
    "boats_yachts": frozenset({"oversized"}),
}

# Profile-name tokens too generic to identify a cargo type on their own.
_GENERIC_PROFILE_NAME_TOKENS = frozenset(
    {
        "or",
        "and",
        "of",
        "the",
        "non",
        "goods",
        "temperature",
        "controlled",
        "standard",
        "dimensions",
        "consumer",
        "lq",
        "hazardous",
        "large",
    }
)


def _matched_vehicle_profiles(
    vehicle_profiles: list[dict[str, Any]],
    active_flags: list[str],
    cargo_description: str | None,
) -> list[dict[str, Any]]:
    active = set(active_flags)
    description_tokens = (
        _text_tokens(cargo_description) if cargo_description else set()
    )

    matched: list[dict[str, Any]] = []
    for profile in vehicle_profiles:
        name = str(profile.get("cargo_profile") or "").strip().lower()
        required_flags = _PROFILE_FLAG_REQUIREMENTS.get(name)
        if required_flags is not None:
            # Flag-driven profile: structured gating only. A cargo description
            # can never substitute for a flag that is unknown or absent.
            if required_flags <= active:
                matched.append(profile)
            continue
        # Profile without flag semantics: exact token overlap between the
        # cargo description and the profile name (no alias expansion).
        name_tokens = _text_tokens(name) - _GENERIC_PROFILE_NAME_TOKENS
        if description_tokens & name_tokens:
            matched.append(profile)
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


def _text_tokens(text: str) -> set[str]:
    # Underscores are split too, so profile names like "steel_coils" produce
    # the same tokens as the description "steel coils".
    return {
        token
        for token in re.split(r"[^a-zA-Z0-9]+", text.lower())
        if token
    }


def _request_unknowns(
    *,
    request: ValidatedShipmentRequest,
    unknown_flags: list[str],
    matched_abnormal_rules: list[dict[str, Any]],
    vehicle_profiles: list[dict[str, Any]],
    standard_limits: list[dict[str, Any]],
    abnormal_rules: list[dict[str, Any]],
) -> tuple[list[Unknown], list[str]]:
    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Road vehicle/load fit requirements may be incomplete.",
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
                impact="Vehicle utilization planning cannot be confirmed.",
            )
        )

    if shipment.dimensions is None:
        missing_fields.append("core_shipment.dimensions")
        unknowns.append(
            Unknown(
                field="core_shipment.dimensions",
                reason="shipment dimensions missing",
                impact="Vehicle loading fit cannot be confirmed.",
            )
        )

    if not vehicle_profiles:
        unknowns.append(
            Unknown(
                field="road_b.vehicle_profiles",
                reason="ROAD-B vehicle profile dataset is empty or malformed",
                impact="Vehicle/load fit reference cannot be checked; do not treat as clear.",
            )
        )

    if not standard_limits:
        unknowns.append(
            Unknown(
                field="road_b.standard_limits",
                reason="ROAD-B standard limits dataset is empty or malformed",
                impact="Road standard limit reference cannot be checked; do not treat as clear.",
            )
        )

    if not abnormal_rules:
        unknowns.append(
            Unknown(
                field="road_b.abnormal_load_rules",
                reason="ROAD-B abnormal load rules dataset is empty or malformed",
                impact="Abnormal road movement requirements cannot be checked; do not treat as clear.",
            )
        )

    if request.cargo_flags.oversized in {FlagState.yes, FlagState.likely}:
        unknowns.append(
            Unknown(
                field="cargo_flags.oversized",
                reason="oversized road cargo requires route/permit/equipment validation",
                impact=(
                    "Special vehicle, permit, escort, or route survey may be "
                    "required."
                ),
            )
        )
        if not matched_abnormal_rules:
            unknowns.append(
                Unknown(
                    field="road_b.abnormal_load_rules",
                    reason=(
                        "oversized cargo flagged but no ROAD-B abnormal load "
                        "rule matched"
                    ),
                    impact=(
                        "Oversized road movement may require permit/escort/"
                        "route survey validation."
                    ),
                )
            )

    return unknowns, missing_fields


def _is_unverified_readiness_gate(record: dict[str, Any]) -> bool:
    # Within standard limits and no abnormal permit: the rule's hard gate is an
    # evidence/readiness requirement (e.g. reefer setpoint evidence, GDP carrier
    # qualification), not a constraint that the shipment already violates.
    return (
        record.get("within_standard_limits") is True
        and record.get("abnormal_permit_required") is False
    )


def _hard_gate_findings(
    records: list[dict[str, Any]],
) -> tuple[list[HardGate], list[Unknown]]:
    gates: list[HardGate] = []
    unknowns: list[Unknown] = []
    for record in records:
        hard_gate = record.get("hard_gate")
        if hard_gate is True:
            basis = _record_basis(record)
            if _is_unverified_readiness_gate(record):
                # No violation detected — only unverified evidence. The gate
                # stays visible at blocking severity but with unknown status,
                # and the evidence gap is reported as an explicit unknown.
                gates.append(
                    HardGate(
                        gate_id=f"ROAD_B_{_gate_token(basis)}_HARD_GATE",
                        mode=RequestedMode.road,
                        severity=GateSeverity.blocking,
                        status=GateStatus.unknown,
                        message=(
                            record.get("hard_gate_reason")
                            or record.get("operational_note")
                            or "ROAD-B vehicle/load fit rule contains a hard gate."
                        ),
                        source_block=BLOCK_ID,
                        basis=basis,
                    )
                )
                unknowns.append(
                    Unknown(
                        field=f"road_b.readiness.{basis}",
                        reason=(
                            "ROAD-B readiness gate for "
                            f"{record.get('cargo_profile') or basis} requires "
                            "validation evidence"
                        ),
                        impact=(
                            "Readiness requirement is unverified; do not treat "
                            "as clear."
                        ),
                    )
                )
                continue
            gates.append(
                HardGate(
                    gate_id=f"ROAD_B_{_gate_token(basis)}_HARD_GATE",
                    mode=RequestedMode.road,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        record.get("hard_gate_reason")
                        or "ROAD-B vehicle/load fit rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=basis,
                )
            )
        elif "hard_gate" in record and not isinstance(hard_gate, bool):
            unknowns.append(
                Unknown(
                    field="road_b.hard_gate",
                    reason="ROAD-B rule has malformed hard_gate",
                    impact="Vehicle/load fit rule cannot be treated as clear.",
                )
            )
    return gates, unknowns


def _record_basis(record: dict[str, Any]) -> str:
    for key in (
        "rule_id",
        "category",
        "cargo_profile_id",
        "cargo_profile",
        "limit_id",
        "parameter",
    ):
        value = record.get(key)
        if value:
            return str(value)
    return "RULE"


def _gate_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return token.upper() if token else "RULE"


def _vehicle_example(record: dict[str, Any]) -> dict[str, Any]:
    example = {
        "vehicle_type": record.get("vehicle_type") or record.get("required_vehicle"),
        "vehicle_name": record.get("vehicle_name") or record.get("cargo_profile"),
        "max_payload_kg": record.get("max_payload_kg"),
        "max_volume_cbm": record.get("max_volume_cbm"),
        "internal_length_m": record.get("internal_length_m"),
        "internal_width_m": record.get("internal_width_m"),
        "internal_height_m": record.get("internal_height_m"),
        "max_length_m": record.get("max_length_m"),
        "max_width_m": record.get("max_width_m"),
        "max_height_m": record.get("max_height_m"),
        "notes": record.get("notes") or record.get("operational_note"),
        "confidence": (
            record.get("confidence")
            or record.get("_confidence")
            or _source_confidence(None).value
        ),
    }
    return {key: value for key, value in example.items() if value is not None}


def _planning_factors(active_flags: list[str]) -> list[str]:
    factors = list(_BASE_PLANNING_FACTORS)
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods road movement requires ADR vehicle, driver, "
            "equipment, documentation, and carrier validation."
        )
    return factors
