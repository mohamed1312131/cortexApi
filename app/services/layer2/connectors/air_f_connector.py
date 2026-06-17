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

BLOCK_ID = "AIR-F"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_f_dataset.json")
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
    "Air border, permit, and customs requirements must be validated with "
    "forwarder/customs broker before booking."
)
_COUNTRY_NAMES = {
    "AE": "United Arab Emirates",
    "CN": "China",
    "DE": "Germany",
    "FR": "France",
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


def fetch_air_f(request: ValidatedShipmentRequest) -> BlockResponse:
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
                    impact="Air border and permit requirements cannot be checked.",
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
    rules = _record_list(dataset, "border_rules")
    jurisdictions = _jurisdictions_for_request(dataset, request)
    matched_rules = _matched_rules(rules, active_flags, jurisdictions)

    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Air border/permit requirements may be incomplete.",
        )
        for flag in unknown_flags
    ]
    if request.commercial.incoterm is None:
        unknowns.append(
            Unknown(
                field="commercial.incoterm",
                reason="incoterm missing",
                impact=(
                    "Responsibility split for export/import permits and "
                    "documents cannot be confirmed."
                ),
            )
        )
    if not matched_rules:
        unknowns.append(
            Unknown(
                field="air_f.border_rules",
                reason="no AIR-F border rules matched this request",
                impact=(
                    "Air border/permit requirements cannot be verified; do not "
                    "treat as clear."
                ),
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
            "required_documents": _unique_documents(matched_rules),
            "permit_or_authorities": _unique_authorities(matched_rules),
            "border_status": "planning_only_requires_forwarder_customs_validation",
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
    trigger_flags = _as_list(rule.get("trigger_flags"))
    if "any_air_shipment" in trigger_flags:
        return True
    if trigger_flags:
        return any(flag in active_flags for flag in trigger_flags)

    cargo_category = _normalize(rule.get("cargo_category"))
    applies_to = _normalize(rule.get("applies_to") or rule.get("applies_to_codes"))
    text = f"{cargo_category} {applies_to}"
    if "any_air_shipment" in active_flags and _is_generic_rule(rule):
        return True
    if "dangerous_goods" in active_flags and any(
        token in text for token in ("dg", "dangerous", "infectious", "biological")
    ):
        return True
    if "pharma" in active_flags and any(
        token in text for token in ("pharma", "medical", "medicine", "pil", "lho")
    ):
        return True
    if "food_perishable" in active_flags and any(
        token in text
        for token in ("food", "perishable", "animal_products", "meat", "seafood")
    ):
        return True
    if "live_animals" in active_flags and any(
        token in text for token in ("live_animals", "avi", "spf", "animal")
    ):
        return True
    if "oversized" in active_flags and any(
        token in text for token in ("oversized", "heavy", "outsized")
    ):
        return True
    if "high_value" in active_flags and any(
        token in text for token in ("valuable", "controlled", "license")
    ):
        return True
    if "temperature_controlled" in active_flags and any(
        token in text for token in ("pharma", "perishable", "food", "cold")
    ):
        return True
    return False


def _rule_jurisdiction_applies(rule: dict[str, Any], jurisdictions: set[str]) -> bool:
    rule_jurisdiction = _normalize(rule.get("jurisdiction"))
    if not rule_jurisdiction:
        return True
    if rule_jurisdiction in {"generic", "global_cites"}:
        return True
    return rule_jurisdiction.upper() in jurisdictions


def _is_generic_rule(rule: dict[str, Any]) -> bool:
    return _normalize(rule.get("jurisdiction")) in {"generic"}


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
                    gate_id=f"AIR_F_{rule_id}_HARD_GATE",
                    mode=RequestedMode.air,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        rule.get("hard_gate_reason")
                        or "Air border/permit rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=rule_id,
                )
            )
        elif hard_gate is False or hard_gate is None:
            # Absent hard_gate = planning/border rule, not a gate. These rules
            # express themselves via required_documents/permits, not a gate
            # boolean, so a missing one is expected — do NOT emit a noise unknown.
            continue
        else:
            unknowns.append(
                Unknown(
                    field="air_f.hard_gate",
                    reason=(
                        f"AIR-F rule {rule_id} has malformed hard_gate "
                        f"(expected true/false, got {hard_gate!r})"
                    ),
                    impact="Border/permit rule cannot be treated as clear.",
                )
            )
    return hard_gates, unknowns


def _unique_documents(rules: list[dict[str, Any]]) -> list[str]:
    documents: list[str] = []
    for rule in rules:
        documents.extend(_as_list(rule.get("required_documents")))
    return _unique(documents)


def _unique_authorities(rules: list[dict[str, Any]]) -> list[str]:
    authorities: list[str] = []
    for rule in rules:
        authorities.extend(_as_list(rule.get("permit_or_authority")))
        authorities.extend(_as_list(rule.get("system_or_authority")))
    return _unique(authorities)


def _jurisdictions_for_request(
    dataset: dict[str, Any],
    request: ValidatedShipmentRequest,
) -> set[str]:
    countries = {
        str(country).upper()
        for country in (
            request.lane.origin_country,
            request.lane.destination_country,
        )
        if country
    }
    country_names = {
        _normalize(_COUNTRY_NAMES.get(country, country))
        for country in countries
    }
    jurisdictions: set[str] = set()
    for row in _record_list(dataset, "airport_jurisdiction_map"):
        row_country = _normalize(
            row.get("country_iso2")
            or row.get("country")
            or row.get("country_name")
        )
        if row_country.upper() in countries or row_country in country_names:
            jurisdiction = row.get("block_f_jurisdiction") or row.get("jurisdiction")
            if jurisdiction:
                jurisdictions.add(str(jurisdiction).upper())
    return jurisdictions


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(";") if item.strip()]
    return []


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


def _normalize(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""
