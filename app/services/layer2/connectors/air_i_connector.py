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

BLOCK_ID = "AIR-I"
DEFAULT_DATA_PATH = Path("data/air/cortex_air_block_i_dataset.json")
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
    "Air route and schedule readiness must be validated with airline/forwarder "
    "before booking."
)
_SCHEDULE_FIELD_MAP = {
    "origin_airport": "lane.origin_city",
    "destination_airport": "lane.destination_city",
    "flight_date": "commercial.ready_date",
    "gross_weight": "core_shipment.weight_kg",
    "piece_count": "core_shipment.quantity",
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


def fetch_air_i(request: ValidatedShipmentRequest) -> BlockResponse:
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
                    impact="Air route readiness cannot be checked.",
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
    feasibility_rules = _matched_rules(
        _record_list(dataset, "route_feasibility_rules"),
        active_flags,
    )
    risk_rules = _matched_rules(
        _record_list(dataset, "route_risk_rules"),
        active_flags,
    )

    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Air route/schedule readiness may be incomplete.",
        )
        for flag in unknown_flags
    ]
    if not request.lane.origin_city:
        unknowns.append(
            Unknown(
                field="lane.origin_city",
                reason="origin city/airport missing",
                impact="Air origin routing cannot be fully checked.",
            )
        )
    if not request.lane.destination_city:
        unknowns.append(
            Unknown(
                field="lane.destination_city",
                reason="destination city/airport missing",
                impact="Air destination routing cannot be fully checked.",
            )
        )
    if not request.commercial.ready_date:
        unknowns.append(
            Unknown(
                field="commercial.ready_date",
                reason="ready date missing",
                impact="Air schedule readiness cannot be checked.",
            )
        )
    if not request.commercial.deadline:
        unknowns.append(
            Unknown(
                field="commercial.deadline",
                reason="deadline missing",
                impact="Transit urgency cannot be assessed.",
            )
        )
    if not feasibility_rules:
        unknowns.append(
            Unknown(
                field="air_i.route_feasibility_rules",
                reason="no AIR-I route feasibility rules matched this request",
                impact=(
                    "Air route readiness cannot be verified; do not treat as clear."
                ),
            )
        )
    unknowns.extend(_schedule_input_unknowns(request, dataset))

    hard_gates, hard_gate_unknowns = _hard_gate_results(feasibility_rules)
    unknowns.extend(hard_gate_unknowns)
    planning_factors = [_BASE_PLANNING_FACTOR]
    planning_factors.extend(_planning_notes(feasibility_rules))
    planning_factors.extend(_planning_notes(risk_rules))

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
            "origin_city": request.lane.origin_city,
            "destination_city": request.lane.destination_city,
            "active_trigger_flags": active_flags,
            "unknown_trigger_flags": unknown_flags,
            "matched_feasibility_rule_ids": [
                rule.get("rule_id") for rule in feasibility_rules if rule.get("rule_id")
            ],
            "matched_risk_ids": [
                rule.get("risk_id") or rule.get("risk_code")
                for rule in risk_rules
                if rule.get("risk_id") or rule.get("risk_code")
            ],
            "route_status": "planning_only_requires_forwarder_airline_schedule_validation",
            "risk_levels": _unique(
                [
                    str(rule.get("risk_level"))
                    for rule in risk_rules
                    if rule.get("risk_level")
                ]
            ),
            "tracking_milestones": _tracking_milestones(dataset),
        },
        hard_gates=hard_gates,
        planning_factors=planning_factors,
        unknowns=unknowns,
        confidence=BlockConfidence(
            source_confidence=(
                SourceConfidence.planning_reference
                if feasibility_rules or risk_rules
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
        if "any_air_shipment" in text or "all air cargo" in text:
            matched.append(rule)
            continue
        if any(_flag_matches_text(flag, text) for flag in active_flags):
            matched.append(rule)
    return matched


def _rule_text(rule: dict[str, Any]) -> str:
    values = [
        rule.get("trigger_conditions"),
        rule.get("route_condition"),
        rule.get("applies_to"),
        rule.get("applies_to_cargo"),
        rule.get("condition"),
    ]
    return _normalize(" ".join(str(value or "") for value in values))


def _flag_matches_text(flag: str, text: str) -> bool:
    flag_tokens = {
        "any_air_shipment": ("any_air_shipment", "all air cargo"),
        "dangerous_goods": ("dangerous_goods", "dg", "cao"),
        "temperature_controlled": (
            "temperature",
            "cold",
            "pharma",
            "crt",
            "col",
            "act",
            "frozen",
        ),
        "oversized": ("oversized", "heavy", "big", "ohg", "large"),
        "high_value": ("high_value", "val", "valuable"),
        "pharma": ("pharma", "pil", "medical", "medicine"),
        "food_perishable": ("food", "perishable", "per", "pes", "pem", "frozen"),
        "live_animals": ("live animals", "live_animals", "avi"),
    }
    return any(token in text for token in flag_tokens.get(flag, (flag,)))


def _schedule_input_unknowns(
    request: ValidatedShipmentRequest,
    dataset: dict[str, Any],
) -> list[Unknown]:
    unknowns: list[Unknown] = []
    seen_fields: set[str] = set()
    for item in _record_list(dataset, "schedule_input_requirements"):
        if not _is_required(item):
            continue
        field_name = str(item.get("field") or item.get("schedule_field") or "")
        field = _SCHEDULE_FIELD_MAP.get(field_name, f"schedule.{field_name}")
        if not field_name or field in seen_fields or _request_has_field(request, field):
            continue
        seen_fields.add(field)
        unknowns.append(
            Unknown(
                field=field,
                reason=str(item.get("reason") or item.get("why_needed") or "schedule input missing"),
                impact=str(item.get("impact") or item.get("missing_impact") or "Air schedule readiness cannot be fully checked."),
            )
        )
    return unknowns


def _is_required(item: dict[str, Any]) -> bool:
    required = item.get("required")
    if required is None:
        return True
    if required is True:
        return True
    return _normalize(required) in {"true", "yes", "required"}


def _request_has_field(request: ValidatedShipmentRequest, field: str) -> bool:
    current: Any = request
    for part in field.split("."):
        current = getattr(current, part, None)
        if current is None:
            return False
    return bool(current)


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
                    gate_id=f"AIR_I_{rule_id}_HARD_GATE",
                    mode=RequestedMode.air,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        rule.get("hard_gate_reason")
                        or "Air route feasibility rule contains a hard gate."
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
                    field="air_i.hard_gate",
                    reason=(
                        f"AIR-I rule {rule_id} has missing or malformed hard_gate"
                    ),
                    impact="Route feasibility rule cannot be treated as clear.",
                )
            )
    return hard_gates, unknowns


def _planning_notes(rules: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for rule in rules:
        note = (
            rule.get("planning_notes")
            or rule.get("message")
            or rule.get("recommended_action")
        )
        if note:
            notes.append(str(note))
    return _unique(notes)


def _tracking_milestones(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []
    for row in _record_list(dataset, "tracking_milestones"):
        milestone = row.get("milestone") or row.get("milestone_code")
        description = row.get("description") or row.get("milestone_name")
        planning_value = row.get("planning_value") or row.get("cortex_interpretation")
        milestones.append(
            {
                "milestone": milestone,
                "description": description,
                "planning_value": planning_value,
            }
        )
    return milestones


def _unique(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        if value not in unique_values:
            unique_values.append(value)
    return unique_values


def _normalize(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""
