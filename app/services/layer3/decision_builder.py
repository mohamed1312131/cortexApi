# app/services/layer3/decision_builder.py
from __future__ import annotations

from app.schemas.block_response import GateSeverity, GateStatus, HardGate, Unknown
from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    CriticReview,
    CriticVerdict,
    DeterministicDecision,
    ReasoningContext,
    ReasoningFactor,
    SafetyGateReport,
)
from app.schemas.reasoning_decision import (
    RankedReadinessOption,
    ReasoningDecision,
)
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.safety_rules import (
    FORBIDDEN_CLAIMS,
    contains_forbidden_claim,
    contains_raw_score_leakage,
)

# Deterministic ReasoningDecision builder (Layer 3 -> Layer 4 seam).
#
# Maps the completed Layer 3 internal objects into the FROZEN ReasoningDecision.
# It never redefines the seam, never exposes raw scores, never embeds the
# InternalScoringTrace, and never lets the Analyst change rank/band/order. It does
# not produce final customer prose — only the structured object Layer 4 will format.
#
# Note on the frozen seam: ReasoningDecision has no evidence_refs / global_hard_gates
# / conflicts / missing_fields fields. So:
#   - hard gates  -> per-option RankedReadinessOption.hard_gates (typed HardGate)
#   - unknowns    -> top-level global_unknowns + per-option unknowns (typed Unknown)
#   - conflicts   -> must_show_warnings (carried from the deterministic decision) +
#                    global_next_actions
#   - missing     -> global_next_actions
#   - evidence_refs are NOT representable in the seam and are intentionally dropped.


_RAW_SCORE_DUMP_TOKENS = ("raw_score", "raw_scores_by_path", "internal_scoring_trace")


# --------------------------------------------------------------------------- #
# ReasoningFactor -> typed seam objects
# --------------------------------------------------------------------------- #
def _parse_details(details: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not details:
        return out
    for part in details.split(" | "):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key] = value
    return out


def _source_block(factor: ReasoningFactor) -> str:
    parsed = _parse_details(factor.details)
    if parsed.get("source_block"):
        return parsed["source_block"]
    for ref in factor.evidence_refs:
        if ref.startswith("gate:"):
            parts = ref.split(":")
            if len(parts) >= 3:
                return parts[1]
    return "unknown_block"


def _to_hard_gate(factor: ReasoningFactor) -> HardGate:
    return HardGate(
        gate_id=factor.code,
        mode=factor.mode,
        severity=GateSeverity(factor.severity),
        status=GateStatus(factor.status or "unknown"),
        message=factor.label,
        source_block=_source_block(factor),
        basis=_parse_details(factor.details).get("basis"),
    )


def _to_unknown(factor: ReasoningFactor) -> Unknown:
    # context_builder stores the unknown's impact in ReasoningFactor.details.
    return Unknown(field=factor.code, reason=factor.label, impact=factor.details)


def _dedup_unknowns(unknowns: list[Unknown]) -> list[Unknown]:
    seen: set[tuple[str, str]] = set()
    out: list[Unknown] = []
    for unknown in unknowns:
        key = (unknown.field, unknown.reason)
        if key not in seen:
            seen.add(key)
            out.append(unknown)
    return out


def _dedup_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _global_next_actions_from_context(context: ReasoningContext) -> list[str]:
    return _dedup_strings(
        [
            f"Resolve hard gate: {g.code}"
            for g in context.hard_gates
            if g.status == "triggered"
        ]
        + [f"Resolve unknown: {u.code}" for u in context.unknowns]
        + [f"Resolve missing field: {m.code}" for m in context.missing_fields]
        + [f"Resolve conflict: {c.code}" for c in context.conflicts]
    )


# --------------------------------------------------------------------------- #
# narrative lookup
# --------------------------------------------------------------------------- #
def _find_narrative(
    analyst_draft: AnalystDraft, rank: int, mode: RequestedMode, path_family: str
) -> AnalystPathNarrative | None:
    for narrative in analyst_draft.narratives:
        if (narrative.rank, narrative.mode, narrative.path_family) == (rank, mode, path_family):
            return narrative
    return None


# --------------------------------------------------------------------------- #
# preconditions
# --------------------------------------------------------------------------- #
def _check_preconditions(
    safety_gate_report: SafetyGateReport, critic_review: CriticReview | None
) -> None:
    if not safety_gate_report.passed:
        raise ValueError("Cannot build a ReasoningDecision: safety gate did not pass.")
    if critic_review is not None and critic_review.verdict in (
        CriticVerdict.revise,
        CriticVerdict.block,
    ):
        raise ValueError(
            f"Cannot build a ReasoningDecision while critic verdict is "
            f"{critic_review.verdict.value!r}; the revision must be resolved first."
        )


# --------------------------------------------------------------------------- #
# post-build validation
# --------------------------------------------------------------------------- #
def _user_facing_text(decision: ReasoningDecision) -> str:
    parts: list[str] = list(decision.allowed_claims) + list(decision.global_next_actions)
    for warning in decision.must_show_warnings:
        parts.append(warning.message)
    for option in decision.ranked_readiness_options:
        parts.append(option.status)
        parts.append(option.why_ranked_here)
        parts.append(option.why_not_higher)
        parts.extend(option.next_actions)
    return " ".join(parts)


def _validate_built_decision(
    built: ReasoningDecision, source: DeterministicDecision
) -> None:
    # rank/mode/path_family must match the deterministic decision exactly
    source_keys = [(p.rank, p.mode, p.path_family) for p in source.ranked_path_families]
    built_keys = [(o.rank, o.mode, o.path_family_id) for o in built.ranked_readiness_options]
    if built_keys != source_keys:
        raise ValueError("Ranked options do not match the deterministic decision (rank/mode/family).")

    for option in built.ranked_readiness_options:
        if option.mode is RequestedMode.unknown:
            raise ValueError("RequestedMode.unknown must never appear in a ReasoningDecision.")
        if not option.why_ranked_here.strip() or not option.why_not_higher.strip():
            raise ValueError(f"Ranked option rank {option.rank} is missing why_ranked_here/why_not_higher.")

    blob = _user_facing_text(built)
    forbidden = contains_forbidden_claim(blob)
    if forbidden:
        raise ValueError(f"Forbidden claim(s) leaked into user-facing fields: {forbidden}")
    leaks = contains_raw_score_leakage(blob)
    if leaks:
        raise ValueError(f"Raw-score leakage in user-facing fields: {leaks}")

    dump = str(built.model_dump())
    for token in _RAW_SCORE_DUMP_TOKENS:
        if token in dump:
            raise ValueError(f"Raw-score token {token!r} leaked into the ReasoningDecision.")


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def build_reasoning_decision(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
    analyst_draft: AnalystDraft,
    safety_gate_report: SafetyGateReport,
    critic_review: CriticReview | None = None,
) -> ReasoningDecision:
    """Map completed Layer 3 objects into the frozen ReasoningDecision seam."""
    _check_preconditions(safety_gate_report, critic_review)

    options: list[RankedReadinessOption] = []
    for path in decision.ranked_path_families:
        narrative = _find_narrative(analyst_draft, path.rank, path.mode, path.path_family)
        if narrative is None:
            raise ValueError(
                f"AnalystDraft has no narrative for ranked path "
                f"(rank={path.rank}, mode={path.mode.value}, family={path.path_family})."
            )
        options.append(
            RankedReadinessOption(
                rank=path.rank,
                path_family_id=path.path_family,
                mode=path.mode,
                # band/order come ONLY from the deterministic decision, never the Analyst
                readiness_band=path.readiness_band,
                status=path.readiness_band.value,
                why_ranked_here=narrative.why_ranked_here,
                why_not_higher=narrative.why_not_higher,
                hard_gates=[_to_hard_gate(f) for f in context.hard_gates if f.mode == path.mode],
                unknowns=[_to_unknown(f) for f in context.unknowns if f.mode == path.mode],
                next_actions=list(narrative.what_would_improve_readiness),
            )
        )

    global_unknowns = _dedup_unknowns([_to_unknown(f) for f in context.unknowns])

    global_next_actions = _global_next_actions_from_context(context)

    # carry the deterministic warnings (already include NOT_FINAL_APPROVAL,
    # BLOCKING_HARD_GATE, CRITICAL_UNKNOWN, CONFLICT_PRESENT, ...) deduped
    must_show_warnings = []
    seen_warnings: set[tuple[str, str]] = set()
    for warning in decision.must_show_warnings:
        key = (warning.code, warning.message)
        if key not in seen_warnings:
            seen_warnings.add(key)
            must_show_warnings.append(warning.model_copy())

    allowed_claims = _dedup_strings(
        [
            f"{option.path_family_id} is ranked #{option.rank} as a preparation-readiness path."
            for option in options
        ]
        + ["This is a preparation-readiness assessment for planning only."]
    )

    built = ReasoningDecision(
        case_id=decision.case_id,
        reasoning_decision_id=decision.internal_trace_ref or f"trace:{decision.case_id}",
        ranking_type=decision.ranking_type,
        ranked_readiness_options=options,
        confidence=decision.confidence_report.model_copy(),
        allowed_claims=allowed_claims,
        forbidden_claims=list(FORBIDDEN_CLAIMS),
        global_unknowns=global_unknowns,
        global_next_actions=global_next_actions,
        must_show_warnings=must_show_warnings,
    )

    _validate_built_decision(built, decision)
    return built


def build_blocked_reasoning_decision(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
) -> ReasoningDecision:
    """Build a safe Layer 4 contract when analyst prose failed safety.

    This uses only deterministic Layer 2/3 facts. It intentionally avoids Analyst
    text because the safety gate already rejected that draft.
    """
    options: list[RankedReadinessOption] = []
    for path in decision.ranked_path_families:
        hard_gates = [f for f in context.hard_gates if f.mode == path.mode]
        unknowns = [f for f in context.unknowns if f.mode == path.mode]
        options.append(
            RankedReadinessOption(
                rank=path.rank,
                path_family_id=path.path_family,
                mode=path.mode,
                readiness_band=path.readiness_band,
                status=path.readiness_band.value,
                why_ranked_here=(
                    "This path is carried forward from deterministic checks for "
                    "a blocked or low-data readiness assessment."
                ),
                why_not_higher=(
                    "It cannot be ranked higher until triggered hard gates, "
                    "critical unknowns, and missing required facts are resolved."
                ),
                hard_gates=[_to_hard_gate(f) for f in hard_gates],
                unknowns=[_to_unknown(f) for f in unknowns],
                next_actions=_global_next_actions_from_context(context),
            )
        )

    global_unknowns = _dedup_unknowns([_to_unknown(f) for f in context.unknowns])
    allowed_claims = _dedup_strings(
        [
            "The current assessment is blocked or low-data based on deterministic checks.",
            "This is a preparation-readiness assessment for planning only.",
        ]
    )
    built = ReasoningDecision(
        case_id=decision.case_id,
        reasoning_decision_id=decision.internal_trace_ref or f"trace:{decision.case_id}",
        ranking_type=decision.ranking_type,
        ranked_readiness_options=options,
        confidence=decision.confidence_report.model_copy(),
        allowed_claims=allowed_claims,
        forbidden_claims=list(FORBIDDEN_CLAIMS),
        global_unknowns=global_unknowns,
        global_next_actions=_global_next_actions_from_context(context),
        must_show_warnings=[warning.model_copy() for warning in decision.must_show_warnings],
    )
    _validate_built_decision(built, decision)
    return built
