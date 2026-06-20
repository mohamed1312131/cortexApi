from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.schemas.layer3 import Layer3Result
from app.schemas.layer4 import Layer4ReportRequest
from app.schemas.reasoning_decision import ReasoningDecision
from app.services.layer2.summary import build_layer2_summary

_MAX_TEXT = 200
_MAX_LIST_ITEMS = 5
_MAX_DICT_ITEMS = 5
_MAX_BLOCK_DATA_DEPTH = 2
_MAX_BLOCKS = 24
_MAX_OPERATIONAL_PATHS = 4
_MAX_OPERATIONAL_ITEMS = 4

LAYER4_FULL_REPORT_PROMPT = """You are Cortex Layer 4, the Transport Readiness Report Agent.

<mission>
Transform structured shipment facts and Layer 3 reasoning into a professional
full transport readiness report for a logistics or transport user.

The user normally spends days checking transport modes, required papers,
restrictions, blockers, carrier questions, schedule boundaries, cost boundaries,
and operational risks. Your job is to turn the provided Cortex data into a clear
chat-style report that the user can act on.
</mission>

<truth_hierarchy>
1. ReasoningDecision is the authority for ranking, readiness bands, confidence,
   hard gates, warnings, allowed claims, and forbidden claims.
2. OperationalEvidence is the authority for path names, route legs, gateway
   validation status, cost boundaries, schedule boundaries, documents,
   handling/safety requirements, blockers, risks, next actions, and limitations.
3. Layer2Summary is supporting rollup/debug context.
4. ShipmentRequest is the user-provided shipment fact source.
5. You control wording, grouping, clarity, and usefulness only.
</truth_hierarchy>

<hard_rules>
- Do not re-rank modes.
- Do not change readiness bands or confidence bands.
- Follow the ReasoningDecision ranking order.
- Use OperationalEvidence path details when present.
- Do not invent documents, quotes, schedules, carrier approvals, customs status,
  terminal acceptance, or legal clearance.
- Do not invent carrier, airline, forwarder, terminal, port, airport, aircraft,
  vessel, document, permit, or authority names.
- Mention specific carrier, airline, forwarder, terminal, port, airport,
  aircraft, vessel, document, permit, or authority names only when they appear in
  the provided Layer2Summary, ReasoningDecision, or shipment request.
- If no specific name is provided, use generic wording such as "the carrier",
  "the airline", "the forwarder", "the origin airport", "the terminal", or
  "the relevant authority".
- Do not hide hard gates, important unknowns, missing checks, or must-show warnings.
- Do not force road/sea/air sections when a mode was not evaluated.
- If a mode was skipped or not covered, mention it only when useful and supported
  by the provided data.
- If cost.status is not_available or unknown, explicitly say cost is unavailable
  or not evidenced and use the provided limitation/missing-input reason.
- If schedule.requires_live_schedule is true, explicitly say the schedule
  requires live validation.
- If gateway candidates are empty, say the gateway could not be resolved from
  current evidence or requires validation, using OperationalEvidence wording.
- If a path status/recommendation_role is blocked, put it under blocked/rejected
  paths and never recommend it.
- Treat mode-specific hard gates as path-scoped. A road-mode hard gate blocks
  Pure Road only; do not call it a global blocker when Sea + Road or Air + Road
  remain evaluable.
- If Pure Road is blocked but other paths remain evaluable, say exactly that.
  Do not say the whole shipment/case is blocked.
- Never treat planning_reference as a live quote.
- Never treat requires_validation as final approval.
- If Layer 3 did not provide a final ReasoningDecision, produce a clarification
  or blocked-assessment answer from the available Layer3Result. Do not call it a
  final readiness report.
- Answer in the language requested by response_language. If response_language is
  "auto", use the language of latest_user_message.
</hard_rules>

<forbidden_claims>
Avoid these claims unless they are explicitly allowed by the ReasoningDecision:
approved, guaranteed, booking confirmed, customs cleared, carrier accepted,
terminal accepted, final legal clearance, final customs clearance, exact price,
confirmed live quote, confirmed live schedule, final booking approval,
customs clearance confirmation, invented vessel details, invented flight details,
invented truck details, will arrive, will clear, best route, optimal route.
</forbidden_claims>

<safe_wording>
Prefer wording like:
strongest preparation path, currently ranked first, requires carrier validation,
requires forwarder confirmation, not booking-ready, not final approval, planning
reference only, live schedule not verified, live quote not verified.
</safe_wording>

<specificity_rules>
- Use exact names and document titles only when present in the input packet.
- If the input contains a generic evidence category but no exact document name,
  describe it generically, for example "dangerous goods declaration or equivalent
  DG paperwork required by the carrier/authority".
- When discussing forwarder/carrier questions, ask about the relevant capability
  or requirement without inventing candidate company names.
- Do not expand abbreviations into legal/document names unless the input provides
  that expansion or it is already stated in the data.
</specificity_rules>

<compact_context_rules>
- The input packet is compacted to reduce token usage.
- Treat counts and "... omitted" markers as transparency, not as permission to invent.
- If an item is omitted from the compact packet, do not make a specific claim about it.
- Use the ReasoningDecision object as the authority for final bands, ranking,
  warnings, hard gates, unknowns, and next actions.
</compact_context_rules>

<report_style>
Write a chat answer, not JSON and not a PDF.
Use concise section headings and bullets.
Be practical and operational.
The report should be detailed enough for a transport professional to use, but
easy to scan.
</report_style>

<target_sections>
Use these sections when relevant:
1. Executive Decision
2. Shipment Summary
3. Ranked Preparation Paths
4. Cost Comparison
5. Schedule Comparison
6. Document Checklist
7. Handling / Safety Requirements
8. Risks and Blockers
9. Recommended Next Actions
10. Limitations
</target_sections>

<blocked_case_rules>
- If the ReasoningDecision ranking_type is blocked_ranking, or every ranked
  option has readiness_band BLOCKED, do not describe any option as "best",
  "recommended", or "preferred".
- In blocked cases, say there is no ready preparation path yet. Then describe
  the current evaluated path and the blockers that must be resolved.
- If a mode is rank #1 only because it is the only evaluated option, explain that
  clearly and do not imply it is operationally better than unevaluated modes.
- Prefer "cannot be treated as booking-ready" or "must be validated before
  booking" over absolute claims like "cannot proceed".
</blocked_case_rules>

<input_packet>
__INPUT_PACKET_JSON__
</input_packet>

Return only the final assistant message. Do not include markdown code fences,
debug notes, or <think> reasoning blocks.
"""


def _value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _truncate_text(value: Any, *, limit: int = _MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 15].rstrip()}... [truncated]"


def _compact_json(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = _MAX_BLOCK_DATA_DEPTH,
) -> Any:
    value = _value(value)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        if depth >= max_depth:
            return {"summary": f"object with {len(value)} keys omitted from compact prompt"}
        items = list(value.items())
        out = {
            str(key): _compact_json(item, depth=depth + 1, max_depth=max_depth)
            for key, item in items[:_MAX_DICT_ITEMS]
        }
        if len(items) > _MAX_DICT_ITEMS:
            out["__omitted_keys__"] = len(items) - _MAX_DICT_ITEMS
        return out
    if isinstance(value, list):
        if depth >= max_depth:
            return [f"list with {len(value)} items omitted from compact prompt"]
        out = [
            _compact_json(item, depth=depth + 1, max_depth=max_depth)
            for item in value[:_MAX_LIST_ITEMS]
        ]
        if len(value) > _MAX_LIST_ITEMS:
            out.append(f"... {len(value) - _MAX_LIST_ITEMS} more omitted from compact prompt")
        return out
    if isinstance(value, str):
        return _truncate_text(value)
    return value


def _compact_hard_gate(gate: Any) -> dict[str, Any]:
    data = gate.model_dump(mode="json", exclude_none=True) if isinstance(gate, BaseModel) else dict(gate)
    return _compact_json(data, max_depth=2)


def _compact_unknown(unknown: Any) -> dict[str, Any]:
    data = unknown.model_dump(mode="json", exclude_none=True) if isinstance(unknown, BaseModel) else dict(unknown)
    return _compact_json(data, max_depth=2)


def _compact_fact_package(request: Layer4ReportRequest) -> dict[str, Any]:
    fact_package = request.fact_package
    blocks = fact_package.block_responses[:_MAX_BLOCKS]
    return {
        "case_id": fact_package.case_id,
        "request": fact_package.request.model_dump(mode="json", exclude_none=True),
        "completeness": fact_package.completeness.model_dump(mode="json"),
        "global_hard_gates": [_compact_hard_gate(gate) for gate in fact_package.global_hard_gates],
        "global_unknowns": [_compact_unknown(item) for item in fact_package.global_unknowns],
        "global_missing_fields": list(fact_package.global_missing_fields),
        "conflicts": [
            conflict.model_dump(mode="json", exclude_none=True)
            for conflict in fact_package.conflicts
        ],
        "derived_rollup": {
            "hard_gates": [
                _compact_hard_gate(gate)
                for gate in fact_package.derived_rollup.hard_gates[:_MAX_LIST_ITEMS]
            ],
            "unknowns": [
                _compact_unknown(item)
                for item in fact_package.derived_rollup.unknowns[:_MAX_LIST_ITEMS]
            ],
            "unknowns_total": len(fact_package.derived_rollup.unknowns),
            "missing_fields": _compact_json(fact_package.derived_rollup.missing_fields),
            "confidence_caps": _compact_json(fact_package.derived_rollup.confidence_caps),
            "modes_covered": [_value(mode) for mode in fact_package.derived_rollup.modes_covered],
            "blocks_called": list(fact_package.derived_rollup.blocks_called),
            "blocks_failed": list(fact_package.derived_rollup.blocks_failed),
            "blocks_empty": list(fact_package.derived_rollup.blocks_empty),
        },
        "block_summaries": [
            {
                "block_id": block.block_id,
                "mode": _value(block.mode),
                "status": _value(block.status),
                "hard_gates": [_compact_hard_gate(gate) for gate in block.hard_gates],
                "unknowns": [_compact_unknown(item) for item in block.unknowns[:_MAX_LIST_ITEMS]],
                "unknowns_total": len(block.unknowns),
                "missing_fields": _compact_json(block.missing_fields),
                "planning_factors": _compact_json(block.planning_factors[:_MAX_LIST_ITEMS]),
                "confidence": block.confidence.model_dump(mode="json"),
                "provenance": {
                    "source": block.provenance.source,
                    "record_id": block.provenance.record_id,
                    "provider_used": _value(block.provenance.provider_used),
                    "fallback_used": block.provenance.fallback_used,
                    "live_data_available": block.provenance.live_data_available,
                },
                "data_keys": list(block.data.keys())[:_MAX_DICT_ITEMS],
                "data_excerpt": (
                    _compact_json(block.data)
                    if _should_include_block_data(block)
                    else "omitted for compact prompt; use status, gates, unknowns, missing_fields, and data_keys only"
                ),
            }
            for block in blocks
        ],
        "block_summaries_total": len(fact_package.block_responses),
        "block_summaries_omitted": max(0, len(fact_package.block_responses) - len(blocks)),
    }


def _compact_reasoning_decision(decision: ReasoningDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    path_scoped_road_blocker = _has_path_scoped_road_blocker_decision(decision)
    cap_reasons = _compact_strings(decision.confidence.cap_reasons)
    if path_scoped_road_blocker:
        cap_reasons = [
            reason
            for reason in cap_reasons
            if "triggered blocking/high hard gate" not in reason.lower()
        ]
        cap_reasons.append(
            "Pure Road is blocked; this road blocker does not apply to Sea + Road or Air + Road."
        )
    return {
        "case_id": decision.case_id,
        "reasoning_decision_id": decision.reasoning_decision_id,
        "ranking_type": _value(decision.ranking_type),
        "path_scoping_note": (
            "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road."
            if path_scoped_road_blocker
            else None
        ),
        "ranked_readiness_options": [
            {
                "rank": option.rank,
                "path_family_id": option.path_family_id,
                "mode": _value(option.mode),
                "readiness_band": _value(option.readiness_band),
                "status": option.status,
                "why_ranked_here": option.why_ranked_here,
                "why_not_higher": option.why_not_higher,
                "hard_gates": [
                    {
                        "gate_id": gate.gate_id,
                        "severity": _value(gate.severity),
                        "status": _value(gate.status),
                        "source_block": gate.source_block,
                    }
                    for gate in option.hard_gates[:3]
                ],
                "hard_gates_total": len(option.hard_gates),
                "unknowns": [
                    {
                        "field": _truncate_text(item.field, limit=80),
                        "reason": _truncate_text(item.reason, limit=120),
                    }
                    for item in option.unknowns[:3]
                ],
                "unknowns_total": len(option.unknowns),
                "next_actions": _compact_strings(option.next_actions),
            }
            for option in decision.ranked_readiness_options
        ],
        "confidence": {
            "band": _value(decision.confidence.band),
            "cap_reasons": cap_reasons,
            "cap_reasons_total": len(decision.confidence.cap_reasons),
        },
        "allowed_claims": _compact_strings(decision.allowed_claims),
        "forbidden_claims_count": len(decision.forbidden_claims),
        "global_unknowns": [
            {
                "field": _truncate_text(item.field, limit=80),
                "reason": _truncate_text(item.reason, limit=120),
            }
            for item in decision.global_unknowns[:3]
        ],
        "global_unknowns_total": len(decision.global_unknowns),
        "global_next_actions": _compact_strings(decision.global_next_actions, limit=6),
        "must_show_warnings": [
            {
                "code": warning.code,
                "message": (
                    "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road."
                    if path_scoped_road_blocker and warning.code == "BLOCKING_HARD_GATE"
                    else _truncate_text(warning.message, limit=180)
                ),
            }
            for warning in decision.must_show_warnings[:_MAX_LIST_ITEMS]
        ],
    }


def _compact_layer3_result(layer3_result: Layer3Result) -> dict[str, Any]:
    return {
        "case_id": layer3_result.case_id,
        "status": _value(layer3_result.status),
        "safety_gate_report": (
            _compact_json(layer3_result.safety_gate_report.model_dump(mode="json"))
            if layer3_result.safety_gate_report is not None
            else None
        ),
    }


def _compact_layer2_support(layer2_summary: Any) -> dict[str, Any]:
    request_summary = _compact_json(layer2_summary.request_summary, max_depth=3)
    active_profiles = _active_profiles_from_request_summary(layer2_summary.request_summary)
    return {
        "case_id": layer2_summary.case_id,
        "request_summary": request_summary,
        "completeness_status": layer2_summary.completeness_status,
        "completeness_reasons": list(layer2_summary.completeness_reasons),
        "modes_covered": list(layer2_summary.modes_covered),
        "hard_gates_total": layer2_summary.hard_gates_total,
        "unknowns_total": layer2_summary.unknowns_total,
        "missing_fields": list(layer2_summary.missing_fields),
        "missing_fields_total": layer2_summary.missing_fields_total,
        "confidence_cap_reasons": _filter_profile_texts(
            list(layer2_summary.confidence_cap_reasons[:_MAX_LIST_ITEMS]),
            active_profiles=active_profiles,
        ),
        "confidence_cap_reasons_total": layer2_summary.confidence_cap_reasons_total,
        "cost_summaries": [
            summary.model_dump(mode="json", exclude_none=True)
            for summary in layer2_summary.cost_summaries[:_MAX_LIST_ITEMS]
        ],
    }


def _compact_strings(values: list[Any], *, limit: int = _MAX_OPERATIONAL_ITEMS) -> list[str]:
    return [
        text
        for item in values[:limit]
        if (text := _truncate_text(item, limit=140)) is not None
    ]


def _active_profiles_from_request_summary(request_summary: Any) -> set[str]:
    if isinstance(request_summary, dict):
        return {str(item) for item in request_summary.get("active_profiles", []) or []}
    return set()


def _filter_profile_texts(values: list[Any], *, active_profiles: set[str]) -> list[str]:
    return [
        text
        for item in values
        if (text := _truncate_text(item, limit=160)) is not None
        and not _is_irrelevant_profile_text(text, active_profiles=active_profiles)
    ]


def _is_irrelevant_profile_text(text: str, *, active_profiles: set[str]) -> bool:
    lowered = text.lower()
    if "general_cargo" not in active_profiles:
        return False
    inactive_terms = {
        "pharma": "pharma",
        "food_perishable": "food_perishable",
        "perishable": "food_perishable",
        "live_animals": "live_animals",
        "live animals": "live_animals",
    }
    return any(term in lowered and profile not in active_profiles for term, profile in inactive_terms.items())


def _compact_source_count(value: Any) -> int:
    refs = getattr(value, "source_refs", None)
    return len(refs or [])


def _compact_shipment(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "cargo_description",
        "weight_kg",
        "volume_cbm",
        "origin_city",
        "origin_country",
        "destination_city",
        "destination_country",
        "ready_date",
        "deadline",
        "requested_mode",
    )
    return {key: value.get(key) for key in keys if value.get(key) is not None}


def _compact_route_leg(leg: Any) -> dict[str, Any]:
    return {
        "leg_type": _value(leg.leg_type),
        "mode": _value(leg.mode),
        "origin": _truncate_text(leg.origin, limit=80),
        "destination": _truncate_text(leg.destination, limit=80),
        "status": _value(leg.status),
        "requires_validation": _compact_strings(leg.requires_validation, limit=2),
        "source_ref_count": _compact_source_count(leg),
    }


def _compact_gateway_evidence(gateways: Any) -> dict[str, Any] | None:
    if gateways is None:
        return None
    return {
        "status": _value(gateways.status),
        "origin_candidates": _compact_strings(gateways.origin_candidates, limit=2),
        "destination_candidates": _compact_strings(gateways.destination_candidates, limit=2),
        "requires_validation": _compact_strings(gateways.requires_validation),
        "source_ref_count": _compact_source_count(gateways),
    }


def _compact_cost_evidence(cost: Any) -> dict[str, Any] | None:
    if cost is None:
        return None
    estimate = (
        cost.estimate.model_dump(mode="json", exclude_none=True)
        if cost.estimate is not None
        else None
    )
    return {
        "status": _value(cost.status),
        "currency": cost.currency,
        "estimate": estimate,
        "basis": _truncate_text(cost.basis, limit=120),
        "limitations": _compact_strings(cost.limitations),
        "missing_inputs": _compact_strings(cost.missing_inputs),
        "source_ref_count": _compact_source_count(cost),
    }


def _compact_schedule_evidence(schedule: Any) -> dict[str, Any] | None:
    if schedule is None:
        return None
    transit_time = (
        schedule.transit_time.model_dump(mode="json", exclude_none=True)
        if schedule.transit_time is not None
        else None
    )
    return {
        "status": _value(schedule.status),
        "ready_date": schedule.ready_date,
        "deadline": schedule.deadline,
        "transit_time": transit_time,
        "feasibility_statement": _truncate_text(schedule.feasibility_statement, limit=160),
        "deadline_fit": _truncate_text(schedule.deadline_fit, limit=120),
        "requires_live_schedule": schedule.requires_live_schedule,
        "limitations": _compact_strings(schedule.limitations),
        "missing_inputs": _compact_strings(schedule.missing_inputs),
        "source_ref_count": _compact_source_count(schedule),
    }


_IRRELEVANT_GENERAL_CARGO_DOCUMENT_TERMS = (
    "death certificate",
    "embalming",
    "coffin",
    "sealing certificate",
    "human remains",
    "animal health",
    "veterinary",
    "pharma",
    "phytosanitary",
    "perishable",
)


def _filter_document_texts(values: list[Any], *, active_profiles: set[str]) -> list[str]:
    out = []
    for item in values:
        text = _truncate_text(item, limit=140)
        if text is None:
            continue
        lowered = text.lower()
        if "general_cargo" in active_profiles and any(
            term in lowered for term in _IRRELEVANT_GENERAL_CARGO_DOCUMENT_TERMS
        ):
            continue
        out.append(text)
    return out[:_MAX_OPERATIONAL_ITEMS]


def _compact_document_evidence(
    documents: Any,
    *,
    active_profiles: set[str],
) -> dict[str, Any] | None:
    if documents is None:
        return None
    return {
        "status": _value(documents.status),
        "required_documents": _filter_document_texts(
            documents.required_documents,
            active_profiles=active_profiles,
        ),
        "conditional_documents": _filter_document_texts(
            documents.conditional_documents,
            active_profiles=active_profiles,
        ),
        "missing_or_unconfirmed": _filter_document_texts(
            documents.missing_or_unconfirmed,
            active_profiles=active_profiles,
        ),
        "limitations": _compact_strings(documents.limitations, limit=3),
        "source_ref_count": _compact_source_count(documents),
    }


def _compact_handling_evidence(handling: Any) -> dict[str, Any] | None:
    if handling is None:
        return None
    return {
        "status": _value(handling.status),
        "requirements": _compact_strings(handling.requirements, limit=2),
        "cargo_fit_notes": _compact_strings(handling.cargo_fit_notes, limit=1),
        "safety_notes": _compact_strings(handling.safety_notes, limit=1),
        "source_ref_count": _compact_source_count(handling),
    }


def _compact_risk_evidence(risk: Any) -> dict[str, Any]:
    return {
        "category": _value(risk.category),
        "severity": _value(risk.severity),
        "message": _truncate_text(risk.message, limit=180),
        "mitigation": _truncate_text(risk.mitigation, limit=160),
        "source_ref_count": _compact_source_count(risk),
    }


def _is_path_scoped_road_blocker_text(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return (
        "road_c_intercontinental_overland_impractical" in lowered
        or "intercontinental overland road corridor" in lowered
        or "pure road" in lowered and "blocked" in lowered
    )


def _has_blocked_pure_road_with_alternatives(paths: list[Any]) -> bool:
    has_blocked_road = any(
        _value(path.primary_mode) == "road"
        and (
            _value(path.status) == "blocked"
            or str(path.readiness_band or "").upper() == "BLOCKED"
            or _value(path.recommendation_role) == "blocked"
        )
        for path in paths
    )
    has_evaluable_alternative = any(
        _value(path.primary_mode) in {"sea", "air"}
        and _value(path.status) != "blocked"
        and str(path.readiness_band or "").upper() != "BLOCKED"
        for path in paths
    )
    return has_blocked_road and has_evaluable_alternative


def _has_path_scoped_road_blocker_decision(decision: ReasoningDecision) -> bool:
    has_blocked_road = any(
        _value(option.mode) == "road"
        and (
            _value(option.readiness_band) == "BLOCKED"
            or "blocked" in str(option.status).lower()
        )
        for option in decision.ranked_readiness_options
    )
    has_evaluable_alternative = any(
        _value(option.mode) in {"sea", "air"}
        and _value(option.readiness_band) != "BLOCKED"
        and "blocked" not in str(option.status).lower()
        for option in decision.ranked_readiness_options
    )
    return has_blocked_road and has_evaluable_alternative


def _compact_operational_evidence(request: Layer4ReportRequest) -> dict[str, Any] | None:
    evidence = request.operational_evidence
    if evidence is None:
        return None
    paths = evidence.paths[:_MAX_OPERATIONAL_PATHS]
    path_scoping_notes = []
    if _has_blocked_pure_road_with_alternatives(evidence.paths):
        path_scoping_notes.append(
            "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road."
        )
        path_scoping_notes.append(
            "The case contains a blocked pure-road path, but other paths remain evaluable."
        )
    active_profiles = _active_profiles_from_request_summary(evidence.shipment)
    return {
        "case_id": evidence.case_id,
        "evidence_version": evidence.evidence_version,
        "generated_from": dict(evidence.generated_from),
        "shipment": _compact_shipment(evidence.shipment),
        "path_scoping_notes": path_scoping_notes,
        "paths": [
            {
                "path_family_id": path.path_family_id,
                "rank": path.rank,
                "primary_mode": _value(path.primary_mode),
                "leg_modes": [_value(mode) for mode in path.leg_modes],
                "display_name": path.display_name,
                "recommendation_role": _value(path.recommendation_role),
                "status": _value(path.status),
                "readiness_band": path.readiness_band,
                "confidence_band": path.confidence_band,
                "evidence_quality": _value(path.evidence_quality),
                "route_legs": [_compact_route_leg(leg) for leg in path.route_legs[:3]],
                "gateways": _compact_gateway_evidence(path.gateways),
                "cost": _compact_cost_evidence(path.cost),
                "schedule": _compact_schedule_evidence(path.schedule),
                "documents": _compact_document_evidence(
                    path.documents,
                    active_profiles=active_profiles,
                ),
                "handling_safety": _compact_handling_evidence(path.handling_safety),
                "blockers": [
                    _compact_risk_evidence(risk)
                    for risk in path.blockers[:2]
                ],
                "risks": [
                    _compact_risk_evidence(risk)
                    for risk in path.risks[:2]
                    if not _is_irrelevant_profile_text(
                        getattr(risk, "message", ""),
                        active_profiles=active_profiles,
                    )
                ],
                "missing_inputs": _compact_strings(path.missing_inputs),
                "next_actions": _compact_strings(path.next_actions, limit=6),
                "limitations": _compact_strings(path.limitations),
            }
            for path in paths
        ],
        "paths_total": len(evidence.paths),
        "paths_omitted": max(0, len(evidence.paths) - len(paths)),
        "global_blockers": [
            _compact_risk_evidence(risk)
            for risk in evidence.global_blockers[:_MAX_OPERATIONAL_ITEMS]
            if not _is_path_scoped_road_blocker_text(getattr(risk, "message", None))
        ],
        "global_unknowns": _filter_profile_texts(
            evidence.global_unknowns,
            active_profiles=active_profiles,
        ),
        "global_limitations": _filter_profile_texts(
            evidence.global_limitations,
            active_profiles=active_profiles,
        ),
    }


def _should_include_block_data(block: Any) -> bool:
    status = _value(block.status)
    return (
        status == "found"
        or bool(block.hard_gates)
        or "COST" in block.block_id
    )


def build_layer4_prompt(request: Layer4ReportRequest) -> str:
    if request.layer2_summary is not None:
        layer2_summary = request.layer2_summary
    elif request.fact_package is not None:
        layer2_summary = build_layer2_summary(request.fact_package)
    else:
        raise ValueError("Layer 4 prompt requires either fact_package or layer2_summary.")
    packet = {
        "report_type": request.report_type.value,
        "latest_user_message": request.latest_user_message,
        "response_language": request.response_language,
        "layer2_support": _compact_layer2_support(layer2_summary),
        "layer3_result": _compact_layer3_result(request.layer3_result),
        "reasoning_decision": _compact_reasoning_decision(request.reasoning_decision),
        "operational_evidence": _compact_operational_evidence(request),
    }
    return LAYER4_FULL_REPORT_PROMPT.replace(
        "__INPUT_PACKET_JSON__",
        json.dumps(packet, ensure_ascii=False, sort_keys=True),
    )
