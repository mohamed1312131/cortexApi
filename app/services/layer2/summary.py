from __future__ import annotations

from typing import Any

from app.schemas.fact_package import FactPackage
from app.schemas.layer2_summary import (
    Layer2BlockSummary,
    Layer2CostSummary,
    Layer2Summary,
)

_MAX_HARD_GATES = 24
_MAX_UNKNOWNS = 24
_MAX_MISSING_FIELDS = 40
_MAX_CONFLICTS = 20
_MAX_CONFIDENCE_REASONS = 24
_MAX_BLOCK_SUMMARIES = 40
_MAX_DATA_KEYS = 12


def build_layer2_summary(fact_package: FactPackage) -> Layer2Summary:
    rollup = fact_package.derived_rollup
    hard_gates = [_hard_gate_summary(gate) for gate in rollup.hard_gates]
    unknowns = [_unknown_summary(unknown) for unknown in rollup.unknowns]
    missing_fields = list(rollup.missing_fields)
    conflicts = [
        conflict.model_dump(mode="json", exclude_none=True)
        for conflict in fact_package.conflicts
    ]
    confidence_reasons = _confidence_cap_reasons(fact_package)
    block_summaries = [_block_summary(block) for block in fact_package.block_responses]

    return Layer2Summary(
        case_id=fact_package.case_id,
        request_summary=_request_summary(fact_package),
        completeness_status=fact_package.completeness.status.value,
        completeness_reasons=list(fact_package.completeness.reasons),
        modes_covered=[mode.value for mode in rollup.modes_covered],
        block_statuses={
            block.block_id: block.status.value for block in fact_package.block_responses
        },
        blocks_called_count=len(rollup.blocks_called),
        blocks_failed=list(rollup.blocks_failed),
        blocks_empty=list(rollup.blocks_empty),
        hard_gates=hard_gates[:_MAX_HARD_GATES],
        hard_gates_total=len(hard_gates),
        unknowns=unknowns[:_MAX_UNKNOWNS],
        unknowns_total=len(unknowns),
        missing_fields=missing_fields[:_MAX_MISSING_FIELDS],
        missing_fields_total=len(missing_fields),
        conflicts=conflicts[:_MAX_CONFLICTS],
        conflicts_total=len(conflicts),
        confidence_cap_reasons=confidence_reasons[:_MAX_CONFIDENCE_REASONS],
        confidence_cap_reasons_total=len(confidence_reasons),
        cost_summaries=_cost_summaries(fact_package),
        block_summaries=block_summaries[:_MAX_BLOCK_SUMMARIES],
        block_summaries_total=len(block_summaries),
        omitted={
            "hard_gates": max(0, len(hard_gates) - _MAX_HARD_GATES),
            "unknowns": max(0, len(unknowns) - _MAX_UNKNOWNS),
            "missing_fields": max(0, len(missing_fields) - _MAX_MISSING_FIELDS),
            "conflicts": max(0, len(conflicts) - _MAX_CONFLICTS),
            "confidence_cap_reasons": max(0, len(confidence_reasons) - _MAX_CONFIDENCE_REASONS),
            "block_summaries": max(0, len(block_summaries) - _MAX_BLOCK_SUMMARIES),
        },
    )


def _request_summary(fact_package: FactPackage) -> dict[str, Any]:
    request = fact_package.request
    return {
        "cargo_description": request.core_shipment.cargo_description,
        "weight_kg": request.core_shipment.weight_kg,
        "volume_cbm": request.core_shipment.volume_cbm,
        "origin_city": request.lane.origin_city,
        "origin_country": request.lane.origin_country,
        "destination_city": request.lane.destination_city,
        "destination_country": request.lane.destination_country,
        "requested_mode": request.mode.requested_mode.value,
        "candidate_modes": [mode.value for mode in request.mode.candidate_modes],
        "active_profiles": list(request.active_profiles),
        "dangerous_goods": request.cargo_flags.dangerous_goods.value,
        "priority": request.user_goal.priority.value,
        "ready_for_layer_2": request.ready_for_layer_2,
        "missing_fields": request.missing_fields.model_dump(mode="json"),
    }


def _hard_gate_summary(gate: Any) -> dict[str, Any]:
    return {
        "gate_id": gate.gate_id,
        "mode": gate.mode.value,
        "severity": gate.severity.value,
        "status": gate.status.value,
        "message": gate.message,
        "source_block": gate.source_block,
        "basis": gate.basis,
    }


def _unknown_summary(unknown: Any) -> dict[str, Any]:
    return unknown.model_dump(mode="json", exclude_none=True)


def _confidence_cap_reasons(fact_package: FactPackage) -> list[str]:
    reasons: list[str] = []
    seen: set[str] = set()
    for cap in fact_package.derived_rollup.confidence_caps:
        for reason in cap.reasons:
            value = f"{cap.source_block}: {reason}" if cap.source_block else reason
            if value not in seen:
                seen.add(value)
                reasons.append(value)
    return reasons


def _block_summary(block: Any) -> Layer2BlockSummary:
    return Layer2BlockSummary(
        block_id=block.block_id,
        mode=block.mode.value,
        status=block.status.value,
        hard_gate_count=len(block.hard_gates),
        unknown_count=len(block.unknowns),
        missing_field_count=len(block.missing_fields),
        planning_factor_count=len(block.planning_factors),
        confidence_source=block.confidence.source_confidence.value,
        confidence_cap=block.confidence.cap,
        data_keys=list(block.data.keys())[:_MAX_DATA_KEYS],
    )


def _cost_summaries(fact_package: FactPackage) -> list[Layer2CostSummary]:
    summaries: list[Layer2CostSummary] = []
    for block in fact_package.block_responses:
        if "COST" not in block.block_id:
            continue
        data = block.data
        summaries.append(
            Layer2CostSummary(
                block_id=block.block_id,
                mode=block.mode.value,
                status=block.status.value,
                cost_status=_string_or_none(data.get("cost_status")),
                estimate=_cost_estimate(data),
                basis=_cost_basis(data),
                currency=_cost_currency(data),
            )
        )
    return summaries


def _cost_estimate(data: dict[str, Any]) -> dict[str, Any] | None:
    for key in (
        "estimated_cost_usd",
        "cost_range",
        "planning_range",
        "estimated_range",
    ):
        value = data.get(key)
        if isinstance(value, dict):
            return dict(value)
    return None


def _cost_basis(data: dict[str, Any]) -> str | None:
    for key in ("rate_basis", "cost_basis", "cost_type"):
        value = _string_or_none(data.get(key))
        if value:
            return value
    return None


def _cost_currency(data: dict[str, Any]) -> str | None:
    for key in ("currency", "estimate_currency"):
        value = _string_or_none(data.get(key))
        if value:
            return value
    if data.get("estimated_cost_usd") is not None:
        return "USD"
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
