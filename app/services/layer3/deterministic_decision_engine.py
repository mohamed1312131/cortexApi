# app/services/layer3/deterministic_decision_engine.py
from __future__ import annotations

from app.schemas.internal_scoring_trace import InternalScoringTrace, ScoringStep
from app.schemas.layer3 import (
    DeterministicDecision,
    RankedPathFamilyDecision,
    ReasoningContext,
    ReasoningFactor,
)
from app.schemas.reasoning_decision import (
    ConfidenceBand,
    ConfidenceReport,
    MustShowWarning,
    RankingType,
    ReadinessBand,
)
from app.schemas.shipment_request import RequestedMode

# Deterministic Layer 3 decision engine.
#
# Input:  ReasoningContext (Step 3 read-model)
# Output: (DeterministicDecision, InternalScoringTrace)
#
# This is the RANKING BRAIN. The LLM never decides readiness or ranking — it only
# explains this result later. Pure Python: no LLM, no randomness, no I/O, no
# clocks. The same ReasoningContext yields identical model_dump()s (modulo an
# explicitly-changed trace_id). Raw numeric scores live ONLY in the
# InternalScoringTrace; DeterministicDecision references the trace by id only.
#
# Gate status is read from ReasoningFactor.status (a first-class field), never by
# parsing ReasoningFactor.details.


# ---- band ordering (strength ascending) ----
_BAND_ORDER: dict[ReadinessBand, int] = {
    ReadinessBand.BLOCKED: 0,
    ReadinessBand.SPECIALIZED_STUDY_REQUIRED: 1,
    ReadinessBand.LOW: 2,
    ReadinessBand.MEDIUM_LOW: 3,
    ReadinessBand.MEDIUM: 4,
    ReadinessBand.HIGH: 5,
}
# Informational raw score per band (trace only — never surfaced on the decision).
_BAND_SCORE: dict[ReadinessBand, float] = {
    band: order / 5.0 for band, order in _BAND_ORDER.items()
}

# Mode tie-breaker for stable ranking: road, then sea, then air (documented).
_MODE_TIEBREAK: dict[RequestedMode, int] = {
    RequestedMode.road: 0,
    RequestedMode.sea: 1,
    RequestedMode.air: 2,
}

_PATH_FAMILY_NAME: dict[RequestedMode, str] = {
    RequestedMode.road: "road_preparation",
    RequestedMode.sea: "sea_preparation",
    RequestedMode.air: "air_preparation",
}

_HIGH_SEVERITIES = {"high", "critical"}
_DG_PROFILES = {"dangerous_goods", "lithium_battery"}


def _weaker(a: ReadinessBand, b: ReadinessBand) -> ReadinessBand:
    """Return the lower (weaker) of two readiness bands."""
    return a if _BAND_ORDER[a] <= _BAND_ORDER[b] else b


# --------------------------------------------------------------------------- #
# per-cap rules
# --------------------------------------------------------------------------- #
def _gate_cap(severity: str, status: str | None) -> ReadinessBand | None:
    if status == "triggered":
        if severity == "blocking":
            return ReadinessBand.BLOCKED
        if severity in _HIGH_SEVERITIES:
            return ReadinessBand.LOW
        if severity == "medium":
            return ReadinessBand.MEDIUM_LOW
        if severity == "low":
            return ReadinessBand.MEDIUM
        return None
    if status == "unknown":
        if severity == "blocking" or severity in _HIGH_SEVERITIES:
            return ReadinessBand.SPECIALIZED_STUDY_REQUIRED
        return ReadinessBand.MEDIUM_LOW
    # not_triggered (or missing) gates do not penalize; recorded in trace only.
    return None


def _unknown_cap(severity: str, has_dg: bool) -> tuple[ReadinessBand, bool]:
    """Return (cap_band, is_critical)."""
    if has_dg:
        # DG/lithium shipments with ANY unresolved info are treated as critical.
        return ReadinessBand.LOW, True
    if severity in _HIGH_SEVERITIES:
        return ReadinessBand.MEDIUM_LOW, True
    return ReadinessBand.MEDIUM, False


def _missing_cap(severity: str) -> ReadinessBand | None:
    if severity == "blocking":
        return ReadinessBand.SPECIALIZED_STUDY_REQUIRED
    if severity == "can_wait":
        # can_wait gaps are non-blocking by definition: visible in trace and on the
        # path, but they must not pull a clean path down from HIGH on their own.
        return None
    # high_value / global / block-level missing data caps below HIGH.
    return ReadinessBand.MEDIUM


# Conflicting facts must never yield confident readiness, but a conflict is a
# readiness/safety cap — NOT a final legal/compliance failure.
_CONFLICT_CAP = ReadinessBand.SPECIALIZED_STUDY_REQUIRED


def _completeness_cap(status: str | None) -> ReadinessBand | None:
    if status == "blocked":
        return ReadinessBand.SPECIALIZED_STUDY_REQUIRED
    if status == "insufficient":
        return ReadinessBand.MEDIUM_LOW
    if status == "incomplete_but_usable":
        return ReadinessBand.MEDIUM
    return None


# --------------------------------------------------------------------------- #
# per-mode computation
# --------------------------------------------------------------------------- #
class _PathComputation:
    __slots__ = (
        "mode",
        "path_family",
        "band",
        "raw_score",
        "applied_caps",
        "blocking_factors",
        "unknown_factors",
        "missing_fields",
        "evidence_refs",
        "steps",
        "notes",
        "triggered_blocking_high",
        "critical_unknown_count",
        "missing_count",
        "conflict_count",
    )

    def __init__(self, mode: RequestedMode) -> None:
        self.mode = mode
        self.path_family = _PATH_FAMILY_NAME[mode]
        self.band = ReadinessBand.HIGH
        self.raw_score = _BAND_SCORE[ReadinessBand.HIGH]
        self.applied_caps: list[str] = []
        self.blocking_factors: list[str] = []
        self.unknown_factors: list[str] = []
        self.missing_fields: list[str] = []
        self.evidence_refs: list[str] = []
        self.steps: list[ScoringStep] = []
        self.notes: list[str] = []
        self.triggered_blocking_high = 0
        self.critical_unknown_count = 0
        self.missing_count = 0
        self.conflict_count = 0


def _mode_evidence_refs(
    context: ReasoningContext,
    mode: RequestedMode,
    gates: list[ReasoningFactor],
    unknowns: list[ReasoningFactor],
    missing: list[ReasoningFactor],
    conflicts: list[ReasoningFactor],
) -> list[str]:
    """Real, deduped evidence refs for a path. Never invents refs."""
    refs: list[str] = []
    seen: set[str] = set()

    def _add_many(values: list[str]) -> None:
        for value in values:
            if value not in seen:
                seen.add(value)
                refs.append(value)

    # block refs for this mode first
    _add_many(
        [ref.ref_id for ref in context.evidence_refs if ref.source_type == "block" and ref.mode == mode]
    )
    for factor in gates:
        _add_many(factor.evidence_refs)
    for factor in unknowns:
        _add_many(factor.evidence_refs)
    for factor in missing:
        _add_many(factor.evidence_refs)
    for factor in conflicts:
        _add_many(factor.evidence_refs)
    return refs


def _compute_path(
    context: ReasoningContext,
    mode: RequestedMode,
    has_dg: bool,
) -> _PathComputation:
    comp = _PathComputation(mode)

    gates = [g for g in context.hard_gates if g.mode == mode]
    unknowns = [u for u in context.unknowns if u.mode is None or u.mode == mode]
    missing = list(context.missing_fields)  # global, applies to every path
    conflicts = [c for c in context.conflicts if c.mode is None or c.mode == mode]

    comp.evidence_refs = _mode_evidence_refs(context, mode, gates, unknowns, missing, conflicts)

    def _apply(cap: ReadinessBand, step_name: str, *, factor: ReasoningFactor | None, label: str) -> None:
        comp.band = _weaker(comp.band, cap)
        comp.raw_score = _BAND_SCORE[comp.band]
        comp.applied_caps.append(label)
        comp.steps.append(
            ScoringStep(
                step_name=step_name,
                path_family=comp.path_family,
                mode=mode,
                input_refs=list(factor.evidence_refs) if factor else [],
                raw_score=comp.raw_score,
                applied_cap=cap.value,
                resulting_band=comp.band,
                reason=label,
            )
        )

    # initial neutral step
    comp.steps.append(
        ScoringStep(
            step_name="initial",
            path_family=comp.path_family,
            mode=mode,
            raw_score=comp.raw_score,
            resulting_band=comp.band,
            reason="neutral ceiling",
        )
    )

    # 1. hard gates
    for gate in gates:
        cap = _gate_cap(gate.severity, gate.status)
        if gate.status == "triggered" and (
            gate.severity == "blocking" or gate.severity in _HIGH_SEVERITIES
        ):
            comp.blocking_factors.append(gate.code)
            comp.triggered_blocking_high += 1
        if cap is None:
            comp.steps.append(
                ScoringStep(
                    step_name="hard_gate_noop",
                    path_family=comp.path_family,
                    mode=mode,
                    input_refs=list(gate.evidence_refs),
                    raw_score=comp.raw_score,
                    resulting_band=comp.band,
                    reason=f"hard_gate:{gate.code} status={gate.status} no cap",
                )
            )
            continue
        _apply(cap, "hard_gate_cap", factor=gate, label=f"hard_gate:{gate.code}={cap.value}")

    # 2. unknowns
    for unknown in unknowns:
        cap, is_critical = _unknown_cap(unknown.severity, has_dg)
        comp.unknown_factors.append(unknown.code)
        if is_critical:
            comp.critical_unknown_count += 1
        _apply(cap, "unknown_cap", factor=unknown, label=f"unknown:{unknown.code}={cap.value}")

    # 3. missing fields
    for field in missing:
        cap = _missing_cap(field.severity)
        comp.missing_fields.append(field.code)
        comp.missing_count += 1
        if cap is None:
            # can_wait: recorded, but no readiness cap.
            note = f"can_wait missing field:{field.code} no readiness cap"
            comp.notes.append(f"{comp.path_family}: {note}")
            comp.steps.append(
                ScoringStep(
                    step_name="missing_field_noop",
                    path_family=comp.path_family,
                    mode=mode,
                    input_refs=list(field.evidence_refs),
                    raw_score=comp.raw_score,
                    resulting_band=comp.band,
                    reason=note,
                )
            )
            continue
        _apply(cap, "missing_field_cap", factor=field, label=f"missing:{field.code}={cap.value}")

    # 4. conflicts (unresolved conflicting facts cannot yield confident readiness)
    for conflict in conflicts:
        comp.conflict_count += 1
        _apply(
            _CONFLICT_CAP,
            "conflict_cap",
            factor=conflict,
            label=f"conflict:{conflict.code}={_CONFLICT_CAP.value}",
        )

    # 5. completeness
    completeness_cap = _completeness_cap(context.completeness_status)
    if completeness_cap is not None:
        _apply(
            completeness_cap,
            "completeness_cap",
            factor=None,
            label=f"completeness:{context.completeness_status}={completeness_cap.value}",
        )

    # final band step
    comp.steps.append(
        ScoringStep(
            step_name="final_band",
            path_family=comp.path_family,
            mode=mode,
            raw_score=comp.raw_score,
            resulting_band=comp.band,
            reason=f"final band for {comp.path_family}",
        )
    )
    return comp


# --------------------------------------------------------------------------- #
# ranking + ranking type
# --------------------------------------------------------------------------- #
def _sort_key(comp: _PathComputation) -> tuple:
    return (
        -_BAND_ORDER[comp.band],          # 1. band strength descending
        comp.triggered_blocking_high,     # 2. fewer triggered blocking/high gates
        comp.critical_unknown_count,      # 3. fewer critical unknowns
        comp.missing_count,               # 4. fewer missing fields
        _MODE_TIEBREAK.get(comp.mode, 99),  # 5. road, sea, air
    )


def _is_low_data(context: ReasoningContext) -> bool:
    if context.completeness_status in {"insufficient", "blocked"}:
        return True
    return any(field.severity == "blocking" for field in context.missing_fields)


def _ranking_type_for(band: ReadinessBand, low_data: bool) -> RankingType:
    if band is ReadinessBand.BLOCKED:
        return RankingType.blocked_ranking
    if low_data:
        return RankingType.low_data_ranking
    return RankingType.preparation_ranking


def _overall_ranking_type(
    paths: list[RankedPathFamilyDecision], low_data: bool
) -> RankingType:
    if not paths or all(p.readiness_band is ReadinessBand.BLOCKED for p in paths):
        return RankingType.blocked_ranking
    if low_data:
        return RankingType.low_data_ranking
    return RankingType.preparation_ranking


# --------------------------------------------------------------------------- #
# confidence + warnings
# --------------------------------------------------------------------------- #
def _confidence(
    context: ReasoningContext,
    overall_band: ReadinessBand,
    has_blocking_high_gate: bool,
    critical_unknowns: list[ReasoningFactor],
) -> ConfidenceReport:
    reasons: list[str] = []
    seen: set[str] = set()

    def _add(reason: str) -> None:
        if reason not in seen:
            seen.add(reason)
            reasons.append(reason)

    if has_blocking_high_gate:
        _add("triggered blocking/high hard gate(s)")
    for unknown in critical_unknowns:
        _add(f"critical unknown: {unknown.code}")
    for field in context.missing_fields:
        if field.severity in {"blocking", "high_value"}:
            _add(f"missing field: {field.code}")
    for conflict in context.conflicts:
        _add(f"conflict: {conflict.code}")
    if context.completeness_status and context.completeness_status != "complete_enough":
        _add(f"completeness:{context.completeness_status}")
    if not context.evidence_refs:
        _add("low evidence: no evidence refs")
    for reason in context.confidence_cap_reasons:
        _add(reason)

    if overall_band in {ReadinessBand.BLOCKED, ReadinessBand.SPECIALIZED_STUDY_REQUIRED}:
        band = ConfidenceBand.LOW
    elif (
        has_blocking_high_gate
        or critical_unknowns
        or context.conflicts
        or context.completeness_status in {"insufficient", "blocked"}
    ):
        band = ConfidenceBand.LOW
    elif reasons:
        band = ConfidenceBand.MEDIUM
    else:
        band = ConfidenceBand.HIGH

    return ConfidenceReport(band=band, cap_reasons=reasons)


def _warnings(
    context: ReasoningContext,
    has_dg: bool,
    blocking_gates: list[ReasoningFactor],
    critical_unknowns: list[ReasoningFactor],
) -> list[MustShowWarning]:
    warnings: list[MustShowWarning] = []

    if blocking_gates:
        codes = ", ".join(g.code for g in blocking_gates)
        warnings.append(
            MustShowWarning(
                code="BLOCKING_HARD_GATE",
                message=f"Blocking hard gate(s) triggered: {codes}.",
            )
        )
    if critical_unknowns:
        codes = ", ".join(u.code for u in critical_unknowns)
        warnings.append(
            MustShowWarning(
                code="CRITICAL_UNKNOWN",
                message=f"Critical unknown(s) present: {codes}.",
            )
        )
    if context.conflicts:
        codes = ", ".join(c.code for c in context.conflicts)
        warnings.append(
            MustShowWarning(
                code="CONFLICT_PRESENT",
                message=(
                    f"Conflicting facts present ({codes}); readiness is capped pending "
                    "resolution. This is a readiness/safety cap, not a final compliance failure."
                ),
            )
        )
    if context.completeness_status in {"insufficient", "blocked"}:
        warnings.append(
            MustShowWarning(
                code="LOW_COMPLETENESS",
                message=f"Fact completeness is {context.completeness_status}; readiness is capped.",
            )
        )
    if has_dg and context.unknowns:
        warnings.append(
            MustShowWarning(
                code="DANGEROUS_GOODS_UNRESOLVED",
                message="Dangerous-goods/lithium profile with unresolved information; readiness is capped.",
            )
        )
    # Always-on honesty warning (no booking/legal/carrier approval implied).
    warnings.append(
        MustShowWarning(
            code="NOT_FINAL_APPROVAL",
            message="This is a preparation-readiness assessment, not final legal, customs, or carrier approval.",
        )
    )
    return warnings


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def build_deterministic_decision(
    context: ReasoningContext,
    *,
    trace_id: str | None = None,
) -> tuple[DeterministicDecision, InternalScoringTrace]:
    """Compute readiness bands and ranked preparation path families deterministically."""
    effective_trace_ref = trace_id or f"trace:{context.case_id}"
    has_dg = bool(set(context.active_profiles) & _DG_PROFILES)

    modes = context.candidate_modes or context.modes_covered
    # never emit unknown; preserve order, dedupe defensively
    ordered_modes: list[RequestedMode] = []
    for mode in modes:
        if mode is not RequestedMode.unknown and mode not in ordered_modes:
            ordered_modes.append(mode)

    computations: list[_PathComputation] = []
    all_steps: list[ScoringStep] = []
    raw_scores_by_path: dict[str, float] = {}
    notes: list[str] = []

    for mode in ordered_modes:
        comp = _compute_path(context, mode, has_dg)
        if not comp.evidence_refs:
            # No real evidence for this mode → do not invent a path or a ref.
            notes.append(f"skipped {comp.path_family}: no evidence refs")
            continue
        computations.append(comp)
        all_steps.extend(comp.steps)
        notes.extend(comp.notes)
        raw_scores_by_path[comp.path_family] = comp.raw_score

    low_data = _is_low_data(context)

    computations.sort(key=_sort_key)

    ranked: list[RankedPathFamilyDecision] = []
    for rank, comp in enumerate(computations, start=1):
        ranked.append(
            RankedPathFamilyDecision(
                rank=rank,
                path_family=comp.path_family,
                mode=comp.mode,
                readiness_band=comp.band,
                ranking_type=_ranking_type_for(comp.band, low_data),
                evidence_refs=comp.evidence_refs,
                applied_caps=comp.applied_caps,
                blocking_factors=comp.blocking_factors,
                unknown_factors=comp.unknown_factors,
                missing_fields=comp.missing_fields,
            )
        )

    overall_band = ranked[0].readiness_band if ranked else ReadinessBand.BLOCKED
    overall_ranking_type = _overall_ranking_type(ranked, low_data)

    all_steps.append(
        ScoringStep(
            step_name="overall_band_selection",
            raw_score=raw_scores_by_path.get(ranked[0].path_family) if ranked else _BAND_SCORE[ReadinessBand.BLOCKED],
            resulting_band=overall_band,
            reason="overall band = best-ranked path family"
            if ranked
            else "no viable path family with evidence",
        )
    )

    # decision-level factor summaries (copied so the decision never aliases context)
    hard_gate_summary = [
        g.model_copy() for g in context.hard_gates if g.status in {"triggered", "unknown"}
    ]
    blocking_gates = [
        g
        for g in context.hard_gates
        if g.status == "triggered" and (g.severity == "blocking" or g.severity in _HIGH_SEVERITIES)
    ]
    critical_unknowns = [
        u.model_copy()
        for u in context.unknowns
        if has_dg or u.severity in _HIGH_SEVERITIES
    ]

    confidence = _confidence(
        context, overall_band, bool(blocking_gates), critical_unknowns
    )
    warnings = _warnings(context, has_dg, blocking_gates, critical_unknowns)

    decision = DeterministicDecision(
        case_id=context.case_id,
        overall_readiness_band=overall_band,
        ranking_type=overall_ranking_type,
        ranked_path_families=ranked,
        hard_gate_summary=hard_gate_summary,
        critical_unknowns=critical_unknowns,
        confidence_report=confidence,
        must_show_warnings=warnings,
        internal_trace_ref=effective_trace_ref,
    )

    trace = InternalScoringTrace(
        case_id=context.case_id,
        trace_id=effective_trace_ref,
        steps=all_steps,
        raw_scores_by_path=raw_scores_by_path,
        notes=notes,
    )

    return decision, trace
