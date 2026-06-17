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
from app.services.layer2.data_catalog import get_main_asset
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "AIR-H"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_h_dataset.json")
_TRIGGER_FLAGS = (
    "dangerous_goods",
    "temperature_controlled",
    "oversized",
    "high_value",
    "pharma",
    "food_perishable",
    "live_animals",
)
_BASE_PLANNING_FACTOR = (
    "Air security, screening, and advance cargo data requirements must be "
    "validated with airline/forwarder before booking."
)
_EU_COUNTRIES = {
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
}
_COUNTRY_JURISDICTIONS = {
    "AE": "UAE",
    "CA": "CANADA",
    "GB": "UK",
    "US": "US",
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
    active = ["any_air_shipment"]
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


def fetch_air_h(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    origin_country = request.lane.origin_country
    destination_country = request.lane.destination_country
    missing_fields = [
        field
        for field, value in (
            ("lane.origin_country", origin_country),
            ("lane.destination_country", destination_country),
        )
        if not value
    ]

    if missing_fields:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.unknown,
            missing_fields=missing_fields,
            unknowns=[
                Unknown(
                    field=field,
                    reason="origin/destination country missing",
                    impact=(
                        "Air security and screening requirements cannot be "
                        "checked."
                    ),
                )
                for field in missing_fields
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance_for(BLOCK_ID, source),
        )

    dataset = _load_dataset()
    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)
    matched_rules = _matched_rules(
        _record_list(dataset, "jurisdiction_security_rules"),
        active_flags,
        _jurisdictions_for_request(request),
    )
    placi_elements = _placi_required_elements(dataset)

    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Air security/screening requirements may be incomplete.",
        )
        for flag in unknown_flags
    ]
    if not request.core_shipment.cargo_description:
        unknowns.append(
            Unknown(
                field="core_shipment.cargo_description",
                reason="cargo description missing",
                impact=(
                    "Security screening/PLACI data readiness cannot be fully "
                    "checked."
                ),
            )
        )
    if not matched_rules:
        unknowns.append(
            Unknown(
                field="air_h.security_rules",
                reason="no AIR-H security rules matched this request",
                impact="Security requirements cannot be verified; do not treat as clear.",
            )
        )
    if not placi_elements:
        unknowns.append(
            Unknown(
                field="air_h.placi_minimum_data",
                reason="PLACI minimum data reference is missing or empty",
                impact="Advance cargo data readiness cannot be checked.",
            )
        )

    hard_gates, hard_gate_unknowns = _hard_gate_results(matched_rules)
    unknowns.extend(hard_gate_unknowns)
    planning_factors = [_BASE_PLANNING_FACTOR]
    planning_factors.extend(
        str(rule.get("planning_notes"))
        for rule in matched_rules
        if rule.get("planning_notes")
    )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=(
            BlockStatus.found
            if hard_gates
            else BlockStatus.unknown
            if unknowns
            else BlockStatus.found
        ),
        data={
            "origin_country": origin_country,
            "destination_country": destination_country,
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "matched_rule_ids": [
                rule.get("rule_id") for rule in matched_rules if rule.get("rule_id")
            ],
            "required_security_actions": _unique_security_actions(matched_rules),
            "placi_required_elements": placi_elements,
            "security_status": (
                "planning_only_requires_airline_forwarder_security_validation"
            ),
        },
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        confidence=BlockConfidence(
            source_confidence=(
                SourceConfidence.planning_reference
                if matched_rules
                else SourceConfidence.unknown
            ),
        ),
        provenance=provenance_for(BLOCK_ID, source),
    )


def _record_list(dataset: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = dataset.get(key)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _matched_rules(
    rules: list[dict[str, Any]],
    active_flags: list[str],
    jurisdictions: set[str],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in rules:
        if _rule_jurisdiction_applies(rule, jurisdictions) and _rule_trigger_applies(
            rule,
            active_flags,
        ):
            matched.append(rule)
    return matched


def _rule_trigger_applies(rule: dict[str, Any], active_flags: list[str]) -> bool:
    trigger_flags = _split_tokens(rule.get("trigger_flags"))
    if "any_air_shipment" in trigger_flags:
        return True
    if trigger_flags:
        return any(flag in active_flags for flag in trigger_flags)

    applies_to = _normalize(rule.get("applies_to"))
    if "any_air_shipment" in active_flags and any(
        token in applies_to
        for token in (
            "cargo",
            "secure_supply_chain",
            "pre_loading",
            "secured_supply_chain",
            "spx",
            "sco",
            "nsc",
            "scy",
            "shr",
        )
    ):
        return True
    if "high_value" in active_flags and any(
        token in applies_to for token in ("val", "vun", "high")
    ):
        return True
    if "live_animals" in active_flags and "avi" in applies_to:
        return True
    if "dangerous_goods" in active_flags and "dg" in applies_to:
        return True
    return False


def _rule_jurisdiction_applies(rule: dict[str, Any], jurisdictions: set[str]) -> bool:
    rule_jurisdictions = set(_split_tokens(rule.get("jurisdiction")))
    if not rule_jurisdictions:
        return True
    if "global" in rule_jurisdictions:
        return True
    return bool({jurisdiction.lower() for jurisdiction in jurisdictions} & rule_jurisdictions)


def _hard_gate_results(
    rules: list[dict[str, Any]],
) -> tuple[list[HardGate], list[Unknown]]:
    hard_gates: list[HardGate] = []
    unknowns: list[Unknown] = []
    for rule in rules:
        rule_id = str(rule.get("rule_id") or "UNKNOWN_RULE")
        hard_gate = rule.get("hard_gate")
        if hard_gate is True:
            hard_gates.append(
                HardGate(
                    gate_id=f"AIR_H_{rule_id}_HARD_GATE",
                    mode=RequestedMode.air,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        rule.get("hard_gate_reason")
                        or "Air security rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=rule_id,
                )
            )
        elif hard_gate is False or hard_gate is None:
            # Absent hard_gate = planning/security rule, not a gate. These rules
            # express themselves via required_security_action, not a gate
            # boolean, so a missing one is expected — do NOT emit a noise unknown.
            continue
        else:
            unknowns.append(
                Unknown(
                    field="air_h.hard_gate",
                    reason=(
                        f"AIR-H rule {rule_id} has malformed hard_gate "
                        f"(expected true/false, got {hard_gate!r})"
                    ),
                    impact="Security rule cannot be treated as clear.",
                )
            )
    return hard_gates, unknowns


def _unique_security_actions(rules: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for rule in rules:
        actions.extend(_action_list(rule.get("required_security_actions")))
        actions.extend(_action_list(rule.get("required_security_action")))
    return _unique(actions)


def _placi_required_elements(dataset: dict[str, Any]) -> list[str]:
    elements: list[str] = []
    for row in _record_list(dataset, "placi_minimum_data"):
        required = row.get("required")
        if required is False or _normalize(required) in {"false", "no"}:
            continue
        element = row.get("data_element") or row.get("field_name")
        if element:
            elements.append(str(element))
    return _unique(elements)


def _jurisdictions_for_request(request: ValidatedShipmentRequest) -> set[str]:
    jurisdictions = {"GLOBAL"}
    for country in (request.lane.origin_country, request.lane.destination_country):
        if not country:
            continue
        code = country.upper()
        if code in _EU_COUNTRIES:
            jurisdictions.add("EU")
        mapped = _COUNTRY_JURISDICTIONS.get(code)
        if mapped is not None:
            jurisdictions.add(mapped)
    return jurisdictions


def _split_tokens(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize(item) for item in value if _normalize(item)]
    if isinstance(value, str):
        tokens = value.replace(";", ",").split(",")
        return [_normalize(token) for token in tokens if _normalize(token)]
    return []


def _action_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


def _normalize(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""
