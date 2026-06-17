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

BLOCK_ID = "AIR-B"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_b_dataset.json")
_SPECIAL_FLAGS = (
    "temperature_controlled",
    "oversized",
    "high_value",
    "pharma",
    "food_perishable",
    "live_animals",
    "dangerous_goods",
)
_BASE_PLANNING_FACTOR = (
    "Air special handling requirements must be validated with airline/handler "
    "before booking."
)


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


def _active_special_flags(request: ValidatedShipmentRequest) -> dict[str, str]:
    active: dict[str, str] = {}
    for flag in _SPECIAL_FLAGS:
        value = getattr(request.cargo_flags, flag)
        if value in {FlagState.yes, FlagState.likely}:
            active[flag] = value.value
    return active


def _unknown_special_flags(request: ValidatedShipmentRequest) -> list[str]:
    unknowns: list[str] = []
    for flag in _SPECIAL_FLAGS:
        if getattr(request.cargo_flags, flag) == FlagState.unknown:
            unknowns.append(flag)
    return unknowns


def fetch_air_b(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    dataset = _load_dataset()
    active_flags = _active_special_flags(request)
    unknown_flags = _unknown_special_flags(request)

    if not active_flags and not unknown_flags:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.air,
            status=BlockStatus.not_applicable,
            data={"special_handling_required": False, "active_flags": []},
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.authored,
            ),
            provenance=provenance_for(BLOCK_ID, source),
        )

    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Air special handling requirements cannot be fully confirmed.",
        )
        for flag in unknown_flags
    ]

    rules = _record_list(dataset, "category_rules")
    codes = _record_list(dataset, "special_handling_codes")
    if active_flags and not isinstance(dataset.get("category_rules"), list):
        unknowns.append(
            Unknown(
                field="air_b.category_rules",
                reason="AIR-B category_rules missing or malformed",
                impact="Special handling requirements cannot be treated as clear.",
            )
        )

    matched_rules = _matched_rules(rules, active_flags)
    if active_flags and not matched_rules and isinstance(dataset.get("category_rules"), list):
        unknowns.append(
            Unknown(
                field="air_b.category_rules",
                reason="no AIR-B category rule matched active special flags",
                impact="Special handling requirements cannot be treated as clear.",
            )
        )

    required_codes = _required_handling_codes(matched_rules)
    code_details, code_unknowns = _handling_code_details(codes, required_codes)
    unknowns.extend(code_unknowns)
    hard_gates, hard_gate_unknowns = _hard_gate_results(matched_rules)
    unknowns.extend(hard_gate_unknowns)

    data = {
        "special_handling_required": bool(active_flags),
        "active_flags": list(active_flags.keys()),
        "unknown_flags": unknown_flags,
        "matched_categories": [_rule_category(rule) for rule in matched_rules],
        "required_handling_codes": required_codes,
        "handling_code_details": code_details,
    }
    planning_factors = [_BASE_PLANNING_FACTOR]
    planning_factors.extend(
        detail["planning_notes"]
        for detail in code_details
        if detail.get("planning_notes")
    )

    if hard_gates:
        status = BlockStatus.found
    elif unknowns:
        status = BlockStatus.unknown
    elif active_flags:
        status = BlockStatus.found
    else:
        status = BlockStatus.not_applicable

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.air,
        status=status,
        data=data,
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
    active_flags: dict[str, str],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in rules:
        triggers = _rule_trigger_flags(rule)
        if any(flag in active_flags for flag in triggers):
            matched.append(rule)
    return matched


def _rule_trigger_flags(rule: dict[str, Any]) -> list[str]:
    explicit = _as_list(rule.get("trigger_flags"))
    if explicit:
        return explicit

    text = _normalize(
        " ".join(
            str(rule.get(key, ""))
            for key in ("category", "category_id", "category_name")
        )
    )
    triggers: list[str] = []
    if "temperature" in text or "active_uld" in text or "cold" in text:
        triggers.append("temperature_controlled")
    if "pharma" in text or "healthcare" in text or "medical" in text:
        triggers.append("pharma")
    if "perishable" in text:
        triggers.append("food_perishable")
    if "live_animals" in text or "live animals" in text:
        triggers.append("live_animals")
    if any(token in text for token in ("oversized", "outsized", "overhang", "heavy")):
        triggers.append("oversized")
    if any(token in text for token in ("valuable", "vulnerable", "security")):
        triggers.append("high_value")
    if any(token in text for token in ("dangerous", "infectious", "biological")):
        triggers.append("dangerous_goods")
    return _unique(triggers)


def _required_handling_codes(rules: list[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    for rule in rules:
        rule_codes = _as_list(rule.get("required_handling_codes"))
        if not rule_codes:
            rule_codes = _as_list(rule.get("codes"))
        codes.extend(rule_codes)
    return _unique(codes)


def _handling_code_details(
    code_records: list[dict[str, Any]],
    required_codes: list[str],
) -> tuple[list[dict[str, Any]], list[Unknown]]:
    details: list[dict[str, Any]] = []
    unknowns: list[Unknown] = []
    by_code = {
        str(record.get("code")).strip().upper(): record
        for record in code_records
        if record.get("code") is not None
    }

    for code in required_codes:
        record = by_code.get(code.upper())
        if record is None:
            unknowns.append(
                Unknown(
                    field="air_b.special_handling_codes",
                    reason=f"required handling code {code} not found",
                    impact="Special handling evidence is incomplete.",
                )
            )
            continue
        details.append(
            {
                "code": record.get("code"),
                "label": record.get("label") or record.get("description"),
                "category": (
                    record.get("category")
                    or record.get("primary_category")
                    or record.get("category_family")
                ),
                "description": record.get("description"),
                "planning_notes": record.get("planning_notes"),
            }
        )
    return details, unknowns


def _hard_gate_results(
    rules: list[dict[str, Any]],
) -> tuple[list[HardGate], list[Unknown]]:
    hard_gates: list[HardGate] = []
    unknowns: list[Unknown] = []
    for rule in rules:
        category = _rule_category(rule)
        hard_gate = rule.get("hard_gate")
        if hard_gate is True:
            hard_gates.append(
                HardGate(
                    gate_id=f"AIR_B_{_gate_token(category)}_HARD_GATE",
                    mode=RequestedMode.air,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        rule.get("hard_gate_reason")
                        or "Air special handling rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=category,
                )
            )
        elif hard_gate is False:
            continue
        else:
            unknowns.append(
                Unknown(
                    field="air_b.hard_gate",
                    reason=(
                        f"AIR-B rule {category} has missing or malformed "
                        "hard_gate"
                    ),
                    impact="Special handling rule cannot be treated as clear.",
                )
            )
    return hard_gates, unknowns


def _rule_category(rule: dict[str, Any]) -> str:
    category = (
        rule.get("category")
        or rule.get("category_id")
        or rule.get("category_name")
        or "unknown"
    )
    return str(category)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


def _normalize(value: str) -> str:
    return value.strip().lower()


def _gate_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
