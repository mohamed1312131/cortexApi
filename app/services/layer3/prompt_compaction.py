from __future__ import annotations

from collections import Counter
from typing import Any

from app.schemas.layer3 import DeterministicDecision, ReasoningContext, ReasoningFactor

_MAX_TEXT = 140
_MAX_LIST_ITEMS = 8
_MAX_HARD_GATES = 8
_MAX_UNKNOWN_ITEMS = 5
_MAX_MISSING_FIELDS = 12
_MAX_CONFLICTS = 8
_MAX_EVIDENCE_REFS = 12
_MAX_PATH_EVIDENCE_REFS = 5
_MAX_WARNINGS = 8

_IRRELEVANT_GENERAL_CARGO_UNKNOWNS = {
    "cargo_flags.pharma",
    "cargo_flags.food_perishable",
    "cargo_flags.live_animals",
}

_SEVERITY_PRIORITY = {
    "critical": 0,
    "blocking": 1,
    "high": 2,
    "conflict": 3,
    "high_value": 4,
    "medium": 5,
    "unknown": 6,
    "can_wait": 7,
    "low": 8,
}
_STATUS_PRIORITY = {
    "triggered": 0,
    "unknown": 1,
    "not_triggered": 2,
    None: 3,
}


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _short_text(value: Any, *, limit: int = _MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 15].rstrip()}... [truncated]"


def _short_item(value: Any) -> Any:
    if isinstance(value, str):
        return _short_text(value)
    return value


def _short_list(values: list[Any], *, limit: int = _MAX_LIST_ITEMS) -> list[Any]:
    out = [_short_item(value) for value in values[:limit]]
    if len(values) > limit:
        out.append(f"... {len(values) - limit} more omitted from prompt context")
    return out


def compact_path_evidence_refs(refs: list[str]) -> list[str]:
    return list(refs[:_MAX_PATH_EVIDENCE_REFS])


def _compact_factor(factor: ReasoningFactor) -> dict[str, Any]:
    return {
        "code": factor.code,
        "label": _short_text(factor.label),
        "severity": factor.severity,
        "mode": _value(factor.mode),
        "status": factor.status,
        "evidence_refs": compact_path_evidence_refs(factor.evidence_refs),
        "details": _short_text(factor.details),
    }


def _compact_evidence_ref(ref: Any) -> dict[str, Any]:
    data = ref.model_dump(mode="json", exclude_none=True)
    data = {
        key: data[key]
        for key in ("ref_id", "source_type", "source_block", "mode", "field_path", "basis")
        if key in data
    }
    if "basis" in data:
        data["basis"] = _short_text(data["basis"], limit=100)
    return data


def _dedupe_factors(factors: list[ReasoningFactor]) -> list[ReasoningFactor]:
    seen: set[tuple[Any, ...]] = set()
    out: list[ReasoningFactor] = []
    for factor in factors:
        key = (
            factor.code,
            factor.label,
            factor.severity,
            _value(factor.mode),
            factor.status,
            tuple(factor.evidence_refs),
            factor.details,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(factor)
    return out


def _filter_prompt_unknowns(
    factors: list[ReasoningFactor],
    *,
    active_profiles: list[str],
) -> list[ReasoningFactor]:
    if "general_cargo" not in set(active_profiles):
        return factors
    return [
        factor
        for factor in factors
        if factor.code not in _IRRELEVANT_GENERAL_CARGO_UNKNOWNS
    ]


def _factor_counts(factors: list[ReasoningFactor]) -> dict[str, Any]:
    by_severity = Counter(factor.severity for factor in factors)
    by_mode = Counter(str(_value(factor.mode)) for factor in factors)
    return {
        "total": len(factors),
        "by_severity": dict(sorted(by_severity.items())),
        "by_mode": dict(sorted(by_mode.items())),
    }


def _top_factors(factors: list[ReasoningFactor], *, limit: int) -> list[ReasoningFactor]:
    return sorted(
        factors,
        key=lambda factor: (
            _STATUS_PRIORITY.get(factor.status, 4),
            _SEVERITY_PRIORITY.get(str(factor.severity), 9),
            str(_value(factor.mode)),
            factor.code,
        ),
    )[:limit]


def _relevant_evidence_ref_ids(
    context: ReasoningContext,
    decision: DeterministicDecision,
) -> set[str]:
    refs: set[str] = set()
    for path in decision.ranked_path_families:
        refs.update(compact_path_evidence_refs(path.evidence_refs))
    for factor in (
        list(decision.hard_gate_summary)
        + list(decision.critical_unknowns)
        + list(context.hard_gates)
        + list(context.missing_fields)
        + list(context.conflicts)
    ):
        refs.update(factor.evidence_refs)
    return refs


def compact_allowed_evidence_refs(
    context: ReasoningContext,
    decision: DeterministicDecision,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(refs: list[str]) -> None:
        for ref in refs:
            if ref in seen:
                continue
            ordered.append(ref)
            seen.add(ref)
            if len(ordered) >= _MAX_EVIDENCE_REFS:
                return

    for path in decision.ranked_path_families:
        add(compact_path_evidence_refs(path.evidence_refs))
        if len(ordered) >= _MAX_EVIDENCE_REFS:
            return ordered

    priority_factors = (
        list(decision.hard_gate_summary)
        + list(decision.critical_unknowns)
        + list(context.hard_gates)
        + list(context.missing_fields)
        + list(context.conflicts)
    )
    for factor in priority_factors:
        add(list(factor.evidence_refs))
        if len(ordered) >= _MAX_EVIDENCE_REFS:
            return ordered

    remaining = sorted(_relevant_evidence_ref_ids(context, decision) - seen)
    add(remaining)
    return ordered


def compact_reasoning_context_for_prompt(
    context: ReasoningContext,
    decision: DeterministicDecision,
) -> dict[str, Any]:
    relevant_refs = set(compact_allowed_evidence_refs(context, decision))
    evidence_refs = [
        _compact_evidence_ref(ref)
        for ref in context.evidence_refs
        if ref.ref_id in relevant_refs
    ]

    unknowns = _filter_prompt_unknowns(
        _dedupe_factors(context.unknowns),
        active_profiles=context.active_profiles,
    )
    critical_unknown_codes = {factor.code for factor in decision.critical_unknowns}
    important_unknowns = [
        factor
        for factor in unknowns
        if factor.severity in {"critical", "high"} or factor.code in critical_unknown_codes
    ]
    top_unknowns = important_unknowns[:_MAX_UNKNOWN_ITEMS]
    if len(top_unknowns) < _MAX_UNKNOWN_ITEMS:
        for factor in unknowns:
            if factor in top_unknowns:
                continue
            top_unknowns.append(factor)
            if len(top_unknowns) >= _MAX_UNKNOWN_ITEMS:
                break

    hard_gates = _dedupe_factors(context.hard_gates)
    missing_fields = _dedupe_factors(context.missing_fields)
    conflicts = _dedupe_factors(context.conflicts)

    return {
        "case_id": context.case_id,
        "request_summary": context.request_summary,
        "candidate_modes": [_value(mode) for mode in context.candidate_modes],
        "active_profiles": list(context.active_profiles),
        "modes_covered": [_value(mode) for mode in context.modes_covered],
        "completeness_status": context.completeness_status,
        "block_statuses": context.block_statuses,
        "hard_gates_summary": {
            **_factor_counts(hard_gates),
            "shown": min(len(hard_gates), _MAX_HARD_GATES),
            "note": (
                "Only highest-order hard gates are shown to keep the prompt compact."
                if len(hard_gates) > _MAX_HARD_GATES
                else None
            ),
        },
        "hard_gates": [
            _compact_factor(factor)
            for factor in _top_factors(hard_gates, limit=_MAX_HARD_GATES)
        ],
        "unknowns_summary": {
            **_factor_counts(unknowns),
            "shown": len(top_unknowns),
            "note": (
                "Only highest-impact/deduplicated unknowns are shown to keep the prompt compact."
                if len(unknowns) > len(top_unknowns)
                else None
            ),
        },
        "top_unknowns": [_compact_factor(factor) for factor in top_unknowns],
        "missing_fields_summary": {
            **_factor_counts(missing_fields),
            "shown": min(len(missing_fields), _MAX_MISSING_FIELDS),
        },
        "missing_fields": [
            _compact_factor(factor)
            for factor in _top_factors(missing_fields, limit=_MAX_MISSING_FIELDS)
        ],
        "conflicts_summary": {
            **_factor_counts(conflicts),
            "shown": min(len(conflicts), _MAX_CONFLICTS),
        },
        "conflicts": [
            _compact_factor(factor)
            for factor in _top_factors(conflicts, limit=_MAX_CONFLICTS)
        ],
        "confidence_cap_reasons": _short_list(context.confidence_cap_reasons),
        "evidence_refs": evidence_refs,
    }


def compact_deterministic_decision_for_prompt(decision: DeterministicDecision) -> dict[str, Any]:
    return {
        "case_id": decision.case_id,
        "overall_readiness_band": _value(decision.overall_readiness_band),
        "ranking_type": _value(decision.ranking_type),
        "ranked_path_families": [
            {
                "rank": path.rank,
                "path_family": path.path_family,
                "mode": _value(path.mode),
                "readiness_band": _value(path.readiness_band),
                "ranking_type": _value(path.ranking_type),
                "evidence_refs": compact_path_evidence_refs(path.evidence_refs),
                "applied_caps": _short_list(path.applied_caps, limit=8),
                "blocking_factors": _short_list(path.blocking_factors),
                "unknown_factors": _short_list(path.unknown_factors, limit=10),
                "missing_fields": _short_list(path.missing_fields),
            }
            for path in decision.ranked_path_families
        ],
        "hard_gate_summary_counts": _factor_counts(decision.hard_gate_summary),
        "hard_gate_summary": [
            _compact_factor(factor)
            for factor in _top_factors(
                _dedupe_factors(decision.hard_gate_summary),
                limit=_MAX_HARD_GATES,
            )
        ],
        "critical_unknowns_summary": {
            **_factor_counts(decision.critical_unknowns),
            "shown": min(len(decision.critical_unknowns), _MAX_UNKNOWN_ITEMS),
        },
        "critical_unknowns": [
            _compact_factor(factor)
            for factor in _top_factors(
                _dedupe_factors(decision.critical_unknowns),
                limit=_MAX_UNKNOWN_ITEMS,
            )
        ],
        "confidence_report": decision.confidence_report.model_dump(mode="json"),
        "must_show_warnings": [
            warning.model_dump(mode="json")
            for warning in decision.must_show_warnings[:_MAX_WARNINGS]
        ],
    }
