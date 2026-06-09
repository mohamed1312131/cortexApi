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

BLOCK_ID = "ROAD-F"
DEFAULT_DATA_PATH = Path("data/road/road_f_document_requirements.json")
DRIVER_HOURS_RULES_PATH = Path("data/road/road_f_driver_hours_rules.json")
BORDER_BUFFER_REFERENCE_PATH = Path(
    "data/road/road_f_border_buffer_reference.json"
)
REALISTIC_TRANSIT_MODEL_PATH = Path(
    "data/road/road_f_realistic_transit_model.json"
)
CONFIDENCE_RULES_PATH = Path("data/road/road_f_confidence_rules.json")
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
        "ROAD-F is a preparation reference only; documents, driver-hours, "
        "border buffers, and transit feasibility must be validated with "
        "carrier/forwarder."
    ),
    (
        "Road transit timing depends on driver-hours, rests, border delays, "
        "loading/unloading, restrictions, and corridor feasibility."
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


def _load_document_requirements() -> list[dict[str, Any]]:
    return _safe_records(_load_json(_data_path()))


def _load_driver_hours_rules() -> list[dict[str, Any]]:
    return _safe_records(_load_json(DRIVER_HOURS_RULES_PATH))


def _load_border_buffer_reference() -> list[dict[str, Any]]:
    return _safe_records(_load_json(BORDER_BUFFER_REFERENCE_PATH))


def _load_realistic_transit_model() -> dict[str, Any]:
    payload = _load_json(REALISTIC_TRANSIT_MODEL_PATH)
    if isinstance(payload, dict):
        return payload
    return {}


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


def _active_trigger_flags(request: ValidatedShipmentRequest) -> list[str]:
    active = ["any_road_shipment"]
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


def _match_records(
    records: list[dict[str, Any]],
    active_flags: list[str],
) -> list[dict[str, Any]]:
    active = {flag.lower() for flag in active_flags}
    matched: list[dict[str, Any]] = []
    for record in records:
        scope = (
            _tokens(record.get("trigger_flags"))
            | _tokens(record.get("applies_to"))
            | _tokens(record.get("required_when"))
            | _tokens(record.get("when_required"))
        )
        if "any_road_shipment" in scope or active & scope:
            matched.append(record)
    return matched


def fetch_road_f(request: ValidatedShipmentRequest) -> BlockResponse:
    source = str(_data_path())
    document_requirements = _load_document_requirements()
    driver_hours_rules = _load_driver_hours_rules()
    border_buffer_reference = _load_border_buffer_reference()
    transit_model = _load_realistic_transit_model()
    _load_confidence_rules()

    active_flags = _active_trigger_flags(request)
    unknown_flags = _unknown_trigger_flags(request)
    matched_documents = _matched_document_requirements(
        document_requirements,
        active_flags,
    )
    matched_driver_hours = _matched_driver_hours_rules(
        driver_hours_rules,
        active_flags,
    )

    unknowns, missing_fields = _request_unknowns(
        request=request,
        unknown_flags=unknown_flags,
        document_requirements=document_requirements,
        matched_documents=matched_documents,
        driver_hours_rules=driver_hours_rules,
        matched_driver_hours=matched_driver_hours,
        border_buffer_reference=border_buffer_reference,
    )
    hard_gates, hard_gate_unknowns = _hard_gate_findings(
        [
            *matched_documents,
            *border_buffer_reference,
            transit_model,
        ]
    )
    unknowns.extend(hard_gate_unknowns)

    confidence = BlockConfidence(
        source_confidence=(
            SourceConfidence.planning_reference
            if document_requirements or driver_hours_rules
            else SourceConfidence.unknown
        )
    )
    if (
        request.commercial.incoterm is None
        or request.commercial.ready_date is None
        or request.commercial.deadline is None
    ):
        confidence.cap = 0.5
        confidence.reasons.append(
            "ROAD-F preparation confidence capped by missing commercial timing/responsibility fields."
        )

    data = {
        "road_preparation_status": (
            "planning_only_requires_carrier_border_validation"
        ),
        "origin_country": request.lane.origin_country,
        "destination_country": request.lane.destination_country,
        "origin_city": request.lane.origin_city,
        "destination_city": request.lane.destination_city,
        "incoterm": request.commercial.incoterm,
        "ready_date": request.commercial.ready_date,
        "deadline": request.commercial.deadline,
        "active_trigger_flags": active_flags,
        "unknown_trigger_flags": unknown_flags,
        "document_requirement_count": len(document_requirements),
        "driver_hours_rule_count": len(driver_hours_rules),
        "border_buffer_reference_count": len(border_buffer_reference),
        "documents": _document_names(matched_documents),
        "matched_document_requirements": [
            _document_example(record) for record in matched_documents[:10]
        ],
        "matched_driver_hours_rules": [
            _driver_hours_example(record) for record in matched_driver_hours[:10]
        ],
        "border_buffer_examples": [
            _border_buffer_example(record) for record in border_buffer_reference[:5]
        ],
        "realistic_transit_reference": _transit_model_summary(transit_model),
    }

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
        data=data,
        hard_gates=hard_gates,
        planning_factors=_planning_factors(request, active_flags),
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=confidence,
        provenance=provenance_for(BLOCK_ID, source),
    )


def _matched_document_requirements(
    requirements: list[dict[str, Any]],
    active_flags: list[str],
) -> list[dict[str, Any]]:
    matched = _match_records(requirements, active_flags)
    selected = list(matched)
    for record in requirements:
        if _is_base_document(record) and record not in selected:
            selected.append(record)
        if "dangerous_goods" in active_flags and _is_dg_document(record):
            if record not in selected:
                selected.append(record)
    return selected


def _matched_driver_hours_rules(
    rules: list[dict[str, Any]],
    active_flags: list[str],
) -> list[dict[str, Any]]:
    matched = _match_records(rules, active_flags)
    if "any_road_shipment" in active_flags and not matched:
        return list(rules)
    return matched


def _tokens(value: Any) -> set[str]:
    if isinstance(value, list):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    normalized = text.lower()
    tokens = {
        token
        for token in re.split(r"[^a-z0-9_]+", normalized)
        if token
    }
    aliases: set[str] = set()
    if "dangerous" in tokens or "adr" in tokens or "dg" in tokens:
        aliases.add("dangerous_goods")
    if "temperature" in tokens or "refrigerated" in tokens or "reefer" in tokens:
        aliases.add("temperature_controlled")
    if "oversized" in tokens or "permit" in tokens or "escort" in tokens:
        aliases.add("oversized")
    if "high" in tokens and "value" in tokens:
        aliases.add("high_value")
    if "pharma" in tokens or "pharmaceutical" in tokens:
        aliases.add("pharma")
    if "food" in tokens or "perishable" in tokens:
        aliases.add("food_perishable")
    if "live" in tokens and "animals" in tokens:
        aliases.add("live_animals")
    return tokens | aliases


def _is_base_document(record: dict[str, Any]) -> bool:
    text = _record_text(record)
    return any(
        phrase in text
        for phrase in (
            "cmr",
            "commercial invoice",
            "packing list",
        )
    )


def _is_dg_document(record: dict[str, Any]) -> bool:
    text = _record_text(record)
    return "adr" in text or "dangerous goods" in text or "dg" in text


def _record_text(record: dict[str, Any]) -> str:
    return " ".join(str(value or "") for value in record.values()).lower()


def _request_unknowns(
    *,
    request: ValidatedShipmentRequest,
    unknown_flags: list[str],
    document_requirements: list[dict[str, Any]],
    matched_documents: list[dict[str, Any]],
    driver_hours_rules: list[dict[str, Any]],
    matched_driver_hours: list[dict[str, Any]],
    border_buffer_reference: list[dict[str, Any]],
) -> tuple[list[Unknown], list[str]]:
    unknowns = [
        Unknown(
            field=f"cargo_flags.{flag}",
            reason=f"{flag} status is unknown",
            impact="Road document/border/driver-hours readiness may be incomplete.",
        )
        for flag in unknown_flags
    ]
    missing_fields: list[str] = []

    if request.lane.origin_country is None:
        missing_fields.append("lane.origin_country")
        unknowns.append(
            Unknown(
                field="lane.origin_country",
                reason="origin country missing",
                impact="Road border and document requirements cannot be checked.",
            )
        )
    if request.lane.destination_country is None:
        missing_fields.append("lane.destination_country")
        unknowns.append(
            Unknown(
                field="lane.destination_country",
                reason="destination country missing",
                impact="Road border and document requirements cannot be checked.",
            )
        )
    if request.commercial.incoterm is None:
        missing_fields.append("commercial.incoterm")
        unknowns.append(
            Unknown(
                field="commercial.incoterm",
                reason="incoterm missing",
                impact="Responsibility split for documents and border processes cannot be confirmed.",
            )
        )
    if request.commercial.ready_date is None:
        missing_fields.append("commercial.ready_date")
        unknowns.append(
            Unknown(
                field="commercial.ready_date",
                reason="ready date missing",
                impact="Road preparation timing cannot be checked.",
            )
        )
    if request.commercial.deadline is None:
        missing_fields.append("commercial.deadline")
        unknowns.append(
            Unknown(
                field="commercial.deadline",
                reason="deadline missing",
                impact="Road transit urgency cannot be assessed.",
            )
        )

    if not document_requirements or not matched_documents:
        unknowns.append(
            Unknown(
                field="road_f.document_requirements",
                reason="no ROAD-F document requirements matched this request",
                impact="Road document readiness cannot be fully verified; do not treat as clear.",
            )
        )

    if not driver_hours_rules or not matched_driver_hours:
        unknowns.append(
            Unknown(
                field="road_f.driver_hours_rules",
                reason="no ROAD-F driver-hours rules matched this request",
                impact="Driver-hours planning cannot be verified; do not treat as clear.",
            )
        )

    if not border_buffer_reference:
        unknowns.append(
            Unknown(
                field="road_f.border_buffer_reference",
                reason="ROAD-F border buffer reference is empty",
                impact="Border delay buffer cannot be checked.",
            )
        )

    if request.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}:
        if not any(_is_dg_document(record) for record in matched_documents):
            unknowns.append(
                Unknown(
                    field="road_f.dg_documents",
                    reason=(
                        "dangerous goods flagged but no ADR/DG document "
                        "requirement matched"
                    ),
                    impact="ADR document readiness cannot be treated as clear.",
                )
            )

    if request.cargo_flags.oversized in {FlagState.yes, FlagState.likely}:
        unknowns.append(
            Unknown(
                field="cargo_flags.oversized",
                reason="oversized road cargo may require permits/escort/route survey",
                impact=(
                    "Road border/route preparation may require specialized "
                    "validation."
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
                    gate_id=f"ROAD_F_{_gate_token(basis)}_HARD_GATE",
                    mode=RequestedMode.road,
                    severity=GateSeverity.blocking,
                    status=GateStatus.triggered,
                    message=(
                        record.get("hard_gate_reason")
                        or "ROAD-F preparation rule contains a hard gate."
                    ),
                    source_block=BLOCK_ID,
                    basis=basis,
                )
            )
        elif "hard_gate" in record and not isinstance(hard_gate, bool):
            unknowns.append(
                Unknown(
                    field="road_f.hard_gate",
                    reason="ROAD-F rule has malformed hard_gate",
                    impact="Preparation rule cannot be treated as clear.",
                )
            )
    return gates, unknowns


def _record_basis(record: dict[str, Any]) -> str:
    for key in (
        "rule_id",
        "document_code",
        "requirement_id",
        "category",
        "buffer_id",
        "model_id",
    ):
        value = record.get(key)
        if value:
            return str(value)
    return "RULE"


def _gate_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return token.upper() if token else "RULE"


def _document_names(records: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for record in records:
        name = str(record.get("document_name") or record.get("document") or "")
        normalized = name.strip()
        if not normalized:
            continue
        if "cmr" in normalized.lower():
            code = "cmr_waybill"
        elif "commercial invoice" in normalized.lower():
            code = "commercial_invoice"
        elif "packing list" in normalized.lower():
            code = "packing_list"
        elif "adr" in normalized.lower() or "dangerous goods" in normalized.lower():
            code = "ADR transport document / DG declaration"
        else:
            code = normalized
        if code not in names:
            names.append(code)
    return names


def _document_example(record: dict[str, Any]) -> dict[str, Any]:
    example = {
        "document_code": record.get("document_code")
        or record.get("requirement_id"),
        "document_name": record.get("document_name") or record.get("document"),
        "document_type": record.get("document_type")
        or record.get("factor_type"),
        "applies_to": record.get("applies_to"),
        "required_when": record.get("required_when")
        or record.get("when_required"),
        "planning_notes": record.get("planning_notes")
        or record.get("responsible_party"),
        "confidence": record.get("confidence") or record.get("_confidence"),
        "jurisdiction_note": record.get("jurisdiction_note")
        or record.get("_source"),
    }
    return {key: value for key, value in example.items() if value is not None}


def _driver_hours_example(record: dict[str, Any]) -> dict[str, Any]:
    example = {
        "rule_id": record.get("rule_id"),
        "rule_name": record.get("rule_name") or record.get("rule"),
        "applies_to": record.get("applies_to"),
        "planning_notes": record.get("planning_notes")
        or record.get("trigger")
        or record.get("limit"),
        "max_daily_driving_hours": (
            record.get("max_daily_driving_hours")
            or (
                record.get("value")
                if record.get("rule") in {"normal_daily_driving_limit_hours"}
                else None
            )
        ),
        "break_requirement": (
            record.get("break_requirement")
            or (
                f"{record.get('value')} {record.get('unit')}"
                if "break" in str(record.get("rule") or "")
                else None
            )
        ),
        "rest_requirement": (
            record.get("rest_requirement")
            or (
                f"{record.get('value')} {record.get('unit')}"
                if "rest" in str(record.get("rule") or "")
                else None
            )
        ),
        "confidence": record.get("confidence") or record.get("_confidence"),
    }
    return {key: value for key, value in example.items() if value is not None}


def _border_buffer_example(record: dict[str, Any]) -> dict[str, Any]:
    scenario = record.get("scenario")
    delay = None
    if record.get("typical_hours") is not None:
        delay = f"typical {record.get('typical_hours')} hours"
    example = {
        "country_pair": record.get("country_pair"),
        "border_type": record.get("border_type") or record.get("buffer_id"),
        "buffer_note": record.get("buffer_note") or scenario,
        "typical_delay_note": record.get("typical_delay_note") or delay,
        "planning_notes": record.get("planning_notes") or scenario,
        "confidence": record.get("confidence") or record.get("_confidence"),
    }
    return {key: value for key, value in example.items() if value is not None}


def _transit_model_summary(model: dict[str, Any]) -> dict[str, Any]:
    if not model:
        return {}

    return {
        key: value
        for key, value in {
            "model_status": model.get("model_status")
            or model.get("routing_engine_duration_is"),
            "assumptions": model.get("assumptions")
            or model.get("default_parameters"),
            "hard_gate_flags": model.get("hard_gate_flags"),
            "planning_notes": model.get("planning_notes")
            or model.get("purpose"),
            "known_limitations": model.get("known_limitations")
            or "Do not compute exact ETA until route, driver plan, and borders are known.",
        }.items()
        if value is not None
    }


def _planning_factors(
    request: ValidatedShipmentRequest,
    active_flags: list[str],
) -> list[str]:
    factors = list(_BASE_PLANNING_FACTORS)
    if request.commercial.deadline is not None:
        factors.append(
            "Deadline should be checked against driver-hours, rests, border buffers, and realistic operating constraints."
        )
    if "dangerous_goods" in active_flags:
        factors.append(
            "Dangerous goods road movement requires ADR-compliant documents, driver/equipment checks, and carrier validation."
        )
    if "oversized" in active_flags:
        factors.append(
            "Oversized road movement may require permits, escort, route survey, and special timing restrictions."
        )
    return factors
