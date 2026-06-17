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

BLOCK_ID = "SEA-I"
DEFAULT_DATA_PATH = Path("data/sea/sea_i_chokepoints_schedule_readiness.json")
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
    "Sea route, chokepoint, and schedule readiness must be validated with "
    "carrier/forwarder before booking."
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


def _active_trigger_flags(request: ValidatedShipmentRequest) -> list[str]:
    active = ["any_sea_shipment"]
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


def fetch_sea_i(request: ValidatedShipmentRequest) -> BlockResponse:
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
            mode=RequestedMode.sea,
            status=BlockStatus.unknown,
            missing_fields=missing_fields,
            unknowns=[
                Unknown(
                    field=field,
                    reason="origin/destination country missing",
                    impact="Sea route/chokepoint readiness cannot be checked.",
                )
                for field in missing_fields
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance_for(BLOCK_ID, source),
        )

    dataset = _load_dataset()
    chokepoints = _record_list(dataset, "chokepoints")
    active_flags = _active_trigger_flags(request)
    schedule_rules = _matched_rules(
        _record_list(dataset, "schedule_readiness_rules"),
        active_flags,
    )
    unknown_flags = _unknown_trigger_flags(request)

    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Sea schedule/chokepoint readiness may be incomplete.",
        )
        for flag in unknown_flags
    ]
    if not request.lane.origin_city:
        unknowns.append(
            Unknown(
                field="lane.origin_city",
                reason="origin port/city missing",
                impact="Sea origin routing cannot be fully checked.",
            )
        )
    if not request.lane.destination_city:
        unknowns.append(
            Unknown(
                field="lane.destination_city",
                reason="destination port/city missing",
                impact="Sea destination routing cannot be fully checked.",
            )
        )
    if not request.commercial.ready_date:
        unknowns.append(
            Unknown(
                field="commercial.ready_date",
                reason="ready date missing",
                impact="Sea schedule readiness cannot be checked.",
            )
        )
    if not request.commercial.deadline:
        unknowns.append(
            Unknown(
                field="commercial.deadline",
                reason="deadline missing",
                impact="Sea transit urgency cannot be assessed.",
            )
        )
    if not chokepoints:
        unknowns.append(
            Unknown(
                field="sea_i.chokepoints",
                reason="SEA-I chokepoint reference dataset is empty",
                impact="Chokepoint exposure cannot be checked; do not treat as clear.",
            )
        )
    if not schedule_rules:
        unknowns.append(
            Unknown(
                field="sea_i.schedule_readiness_rules",
                reason="no SEA-I schedule readiness rules matched this request",
                impact=(
                    "Sea schedule readiness cannot be verified; do not treat as clear."
                ),
            )
        )

    hard_gates, hard_gate_unknowns = _hard_gate_results(schedule_rules)
    unknowns.extend(hard_gate_unknowns)
    planning_factors = [_BASE_PLANNING_FACTOR]
    planning_factors.extend(_planning_notes(schedule_rules))

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
        data={
            "origin_country": origin_country,
            "destination_country": destination_country,
            "origin_city": request.lane.origin_city,
            "destination_city": request.lane.destination_city,
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "matched_schedule_rule_ids": [
                rule.get("rule_id") for rule in schedule_rules if rule.get("rule_id")
            ],
            "chokepoint_reference_count": len(chokepoints),
            "chokepoint_examples": [
                _chokepoint_example(record) for record in chokepoints[:5]
            ],
            "schedule_status": (
                "planning_only_requires_carrier_forwarder_schedule_validation"
            ),
        },
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        confidence=BlockConfidence(
            source_confidence=(
                SourceConfidence.planning_reference
                if schedule_rules or chokepoints
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
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for rule in rules:
        text = _rule_text(rule)
        if _flag_matches_text("any_sea_shipment", text):
            matched.append(rule)
            continue
        if any(_flag_matches_text(flag, text) for flag in active_flags):
            matched.append(rule)
    return matched


def _rule_text(rule: dict[str, Any]) -> str:
    values = [
        rule.get("trigger_flags"),
        rule.get("applies_to"),
        rule.get("applies_when"),
        rule.get("condition"),
        rule.get("name"),
    ]
    return _normalize(" ".join(str(value or "") for value in values))


def _flag_matches_text(flag: str, text: str) -> bool:
    flag_tokens = {
        "any_sea_shipment": (
            "any_sea_shipment",
            "any sea shipment",
            "all sea shipments",
            "all ocean shipments",
            "all ocean cargo",
        ),
        "dangerous_goods": ("dangerous goods", "dangerous_goods", "dg", "imdg"),
        "temperature_controlled": (
            "temperature",
            "reefer",
            "cold",
            "pharma",
            "perishable",
        ),
        "oversized": (
            "oversized",
            "heavy",
            "breakbulk",
            "oog",
            "special equipment",
        ),
        "high_value": ("high value", "high_value", "valuable"),
        "pharma": ("pharma", "medical", "medicine"),
        "food_perishable": ("food", "perishable", "reefer", "cold"),
        "live_animals": ("live animals", "live_animals"),
    }
    return any(token in text for token in flag_tokens.get(flag, (flag,)))


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
                    gate_id=f"SEA_I_{rule_id}_HARD_GATE",
                    mode=RequestedMode.sea,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        rule.get("hard_gate_reason")
                        or "SEA-I schedule readiness rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=rule_id,
                )
            )
        elif hard_gate is False:
            continue
        else:
            unknowns.append(
                Unknown(
                    field="sea_i.hard_gate",
                    reason=(
                        f"SEA-I rule {rule_id} has missing or malformed hard_gate"
                    ),
                    impact="Schedule readiness rule cannot be treated as clear.",
                )
            )
    return hard_gates, unknowns


def _planning_notes(rules: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for rule in rules:
        note = (
            rule.get("planning_notes")
            or rule.get("readiness_message")
            or rule.get("default_deadline_assumption")
        )
        if note:
            notes.append(str(note))
    return _unique(notes)


def _chokepoint_example(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "name": record.get("name"),
        "waterway_or_area": record.get("waterway_or_area"),
        "region": record.get("region"),
        "risk_types": record.get("risk_types"),
        "typical_planning_impact": record.get("typical_planning_impact"),
        "readiness_action": record.get("readiness_action"),
        "requires_live_validation": record.get("requires_live_validation"),
    }


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


def _normalize(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""
