from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.schemas.layer3 import Layer3Result
from app.schemas.layer4 import Layer4ReportRequest
from app.schemas.reasoning_decision import ReasoningDecision

_MAX_TEXT = 200
_MAX_LIST_ITEMS = 5
_MAX_DICT_ITEMS = 5
_MAX_BLOCK_DATA_DEPTH = 2
_MAX_BLOCKS = 24

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
   warnings, hard gates, and next actions.
2. FactPackage is supporting evidence for documents, constraints, operational
   details, unknowns, route/cost/schedule boundaries, and preparation factors.
3. ShipmentRequest is the shipment fact source.
4. You control wording, grouping, clarity, and usefulness only.
</truth_hierarchy>

<hard_rules>
- Do not re-rank modes.
- Do not change readiness bands or confidence bands.
- Do not invent documents, quotes, schedules, carrier approvals, customs status,
  terminal acceptance, or legal clearance.
- Do not invent carrier, airline, forwarder, terminal, port, airport, aircraft,
  vessel, document, permit, or authority names.
- Mention specific carrier, airline, forwarder, terminal, port, airport,
  aircraft, vessel, document, permit, or authority names only when they appear in
  the provided FactPackage, ReasoningDecision, or shipment request.
- If no specific name is provided, use generic wording such as "the carrier",
  "the airline", "the forwarder", "the origin airport", "the terminal", or
  "the relevant authority".
- Do not hide hard gates, important unknowns, missing checks, or must-show warnings.
- Do not force road/sea/air sections when a mode was not evaluated.
- If a mode was skipped or not covered, mention it only when useful and supported
  by the provided data.
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
confirmed live schedule, will arrive, will clear, best route, optimal route.
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
1. Executive Summary
2. Best Preparation Path, or Current Evaluated Path if every option is blocked
3. Evaluated Modes
4. Mode-by-Mode Details
5. Documents / Paperwork Needed
6. Hard Gates / Blockers
7. Unknowns / Missing Checks
8. Cost and Schedule Boundaries
9. Questions to Ask Forwarder / Carrier
10. Recommended Next Actions
11. Important Warnings
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
    return {
        "case_id": decision.case_id,
        "reasoning_decision_id": decision.reasoning_decision_id,
        "ranking_type": _value(decision.ranking_type),
        "ranked_readiness_options": [
            {
                "rank": option.rank,
                "path_family_id": option.path_family_id,
                "mode": _value(option.mode),
                "readiness_band": _value(option.readiness_band),
                "status": option.status,
                "why_ranked_here": option.why_ranked_here,
                "why_not_higher": option.why_not_higher,
                "hard_gates": [_compact_hard_gate(gate) for gate in option.hard_gates],
                "unknowns": [_compact_unknown(item) for item in option.unknowns[:_MAX_LIST_ITEMS]],
                "unknowns_total": len(option.unknowns),
                "next_actions": _compact_json(option.next_actions),
            }
            for option in decision.ranked_readiness_options
        ],
        "confidence": decision.confidence.model_dump(mode="json"),
        "allowed_claims": list(decision.allowed_claims),
        "forbidden_claims": list(decision.forbidden_claims),
        "global_unknowns": [
            _compact_unknown(item) for item in decision.global_unknowns[:_MAX_LIST_ITEMS]
        ],
        "global_unknowns_total": len(decision.global_unknowns),
        "global_next_actions": _compact_json(decision.global_next_actions),
        "must_show_warnings": [
            warning.model_dump(mode="json") for warning in decision.must_show_warnings
        ],
    }


def _compact_layer3_result(layer3_result: Layer3Result) -> dict[str, Any]:
    return {
        "case_id": layer3_result.case_id,
        "status": _value(layer3_result.status),
        "analyst_draft": (
            _compact_json(layer3_result.analyst_draft.model_dump(mode="json"))
            if layer3_result.analyst_draft is not None
            else None
        ),
        "critic_review": (
            _compact_json(layer3_result.critic_review.model_dump(mode="json"))
            if layer3_result.critic_review is not None
            else None
        ),
        "safety_gate_report": (
            _compact_json(layer3_result.safety_gate_report.model_dump(mode="json"))
            if layer3_result.safety_gate_report is not None
            else None
        ),
        "debug": _compact_json(layer3_result.debug, max_depth=2),
    }


def _should_include_block_data(block: Any) -> bool:
    status = _value(block.status)
    return (
        status == "found"
        or bool(block.hard_gates)
        or "COST" in block.block_id
    )


def build_layer4_prompt(request: Layer4ReportRequest) -> str:
    packet = {
        "report_type": request.report_type.value,
        "latest_user_message": request.latest_user_message,
        "response_language": request.response_language,
        "fact_package": _compact_fact_package(request),
        "layer3_result": _compact_layer3_result(request.layer3_result),
        "reasoning_decision": _compact_reasoning_decision(request.reasoning_decision),
    }
    return LAYER4_FULL_REPORT_PROMPT.replace(
        "__INPUT_PACKET_JSON__",
        json.dumps(packet, ensure_ascii=False, sort_keys=True),
    )
