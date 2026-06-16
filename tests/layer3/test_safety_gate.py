from __future__ import annotations

import inspect

from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    DeterministicDecision,
    EvidenceRef,
    Layer3NextAction,
    RankedPathFamilyDecision,
    ReasoningContext,
    ReasoningFactor,
    SafetyGateStatus,
)
from app.schemas.reasoning_decision import (
    ConfidenceBand,
    ConfidenceReport,
    RankingType,
    ReadinessBand,
)
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.agents import analyst_agent
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision
from app.services.layer3.safety_gate import run_safety_gate


# --------------------------------------------------------------------------- #
# factories
# --------------------------------------------------------------------------- #
def _block_ref(mode: RequestedMode) -> EvidenceRef:
    block_id = f"{mode.value.upper()}-A"
    return EvidenceRef(ref_id=f"block:{block_id}", source_type="block", source_block=block_id, mode=mode)


def _ctx(
    *,
    case_id: str = "case-1",
    candidate_modes: list[RequestedMode] | None = None,
    active_profiles: list[str] | None = None,
    hard_gates: list[ReasoningFactor] | None = None,
    unknowns: list[ReasoningFactor] | None = None,
    missing_fields: list[ReasoningFactor] | None = None,
    conflicts: list[ReasoningFactor] | None = None,
    completeness_status: str | None = "complete_enough",
) -> ReasoningContext:
    candidate_modes = candidate_modes or [RequestedMode.road]
    return ReasoningContext(
        case_id=case_id,
        candidate_modes=candidate_modes,
        modes_covered=candidate_modes,
        active_profiles=active_profiles or [],
        block_statuses={f"{m.value.upper()}-A": "found" for m in candidate_modes},
        hard_gates=hard_gates or [],
        unknowns=unknowns or [],
        missing_fields=missing_fields or [],
        conflicts=conflicts or [],
        confidence_cap_reasons=[],
        evidence_refs=[_block_ref(m) for m in candidate_modes],
        completeness_status=completeness_status,
    )


def _gate(code: str, mode: RequestedMode, severity: str, status: str) -> ReasoningFactor:
    return ReasoningFactor(
        code=code, label=code, severity=severity, mode=mode,
        evidence_refs=[f"gate:{mode.value.upper()}-A:{code}"], status=status,
    )


def _unknown(code: str, *, mode: RequestedMode | None = None, severity: str = "unknown") -> ReasoningFactor:
    src = f"{mode.value.upper()}-A" if mode else "global"
    return ReasoningFactor(code=code, label=code, severity=severity, mode=mode, evidence_refs=[f"unknown:{src}:{code}"])


def _conflict(code: str, *, mode: RequestedMode | None = None) -> ReasoningFactor:
    return ReasoningFactor(code=code, label=code, severity="conflict", mode=mode, evidence_refs=[f"conflict:0:{code}"])


def _missing(code: str, severity: str) -> ReasoningFactor:
    return ReasoningFactor(code=code, label=code, severity=severity, evidence_refs=[f"missing:{code}"])


def _decision(context: ReasoningContext) -> DeterministicDecision:
    decision, _ = build_deterministic_decision(context, trace_id="t1")
    return decision


def _draft_from_decision(decision: DeterministicDecision) -> AnalystDraft:
    """A faithful draft: one narrative per path, citing the path's own evidence."""
    narratives = [
        AnalystPathNarrative(
            path_family=p.path_family,
            mode=p.mode,
            rank=p.rank,
            why_ranked_here="Ranked here per the cited evidence and applied caps.",
            why_not_higher="Held below the top band by the cited factors.",
            what_would_improve_readiness=["Resolve the cited gaps."],
            evidence_refs=list(p.evidence_refs),
        )
        for p in decision.ranked_path_families
    ]
    return AnalystDraft(
        case_id=decision.case_id,
        narratives=narratives,
        overall_summary="Internal explanation of the deterministic readiness result.",
        next_action_summary="Resolve the cited gaps.",
    )


def _codes(report) -> set[str]:
    return {v.code for v in report.violations}


# --------------------------------------------------------------------------- #
# 1. valid passes
# --------------------------------------------------------------------------- #
def test_valid_draft_passes():
    ctx = _ctx(hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")])
    decision = _decision(ctx)
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=_draft_from_decision(decision))
    assert report.status is SafetyGateStatus.pass_
    assert report.passed is True
    assert report.next_action is Layer3NextAction.pass_to_layer4
    assert report.violations == []


# --------------------------------------------------------------------------- #
# 2. case mismatch blocks
# --------------------------------------------------------------------------- #
def test_case_mismatch_blocks():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.case_id = "other-case"
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "CASE_ID_MISMATCH" in _codes(report)


# --------------------------------------------------------------------------- #
# 3-6. coverage / re-rank
# --------------------------------------------------------------------------- #
def test_omitted_narrative_revises():
    ctx = _ctx(candidate_modes=[RequestedMode.road, RequestedMode.sea])
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives.pop()
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.revise
    assert "OMITTED_NARRATIVE" in _codes(report)


def test_extra_narrative_revises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives.append(
        AnalystPathNarrative(
            path_family="air_preparation", mode=RequestedMode.air, rank=99,
            why_ranked_here="x", why_not_higher="y", evidence_refs=["block:ROAD-A"],
        )
    )
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.revise
    assert "EXTRA_NARRATIVE" in _codes(report)


def test_duplicate_narrative_revises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives.append(draft.narratives[0].model_copy())
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.revise
    assert "DUPLICATE_NARRATIVE" in _codes(report)


def test_changed_rank_revises():
    ctx = _ctx(candidate_modes=[RequestedMode.road, RequestedMode.sea])
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].rank = 99
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status in {SafetyGateStatus.revise, SafetyGateStatus.block}
    assert _codes(report) & {"OMITTED_NARRATIVE", "EXTRA_NARRATIVE"}


# --------------------------------------------------------------------------- #
# 7-8. evidence discipline
# --------------------------------------------------------------------------- #
def test_evidence_outside_allowed_blocks():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].evidence_refs = ["block:FAKE"]
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "EVIDENCE_NOT_ALLOWED" in _codes(report)


def test_empty_evidence_revises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].evidence_refs = []
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.revise
    assert "EMPTY_EVIDENCE" in _codes(report)


# --------------------------------------------------------------------------- #
# 9-11. visibility of gates / unknowns / conflicts
# --------------------------------------------------------------------------- #
def test_hidden_triggered_blocking_gate_blocks():
    ctx = _ctx(hard_gates=[_gate("GB", RequestedMode.road, "blocking", "triggered")])
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    # hide the gate: cite only the block ref, omit the gate ref + code
    draft.narratives[0].evidence_refs = ["block:ROAD-A"]
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "HIDDEN_HARD_GATE" in _codes(report)


def test_hidden_high_unknown_revises():
    ctx = _ctx(unknowns=[_unknown("classification", mode=RequestedMode.road, severity="high")])
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].evidence_refs = ["block:ROAD-A"]
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status in {SafetyGateStatus.revise, SafetyGateStatus.block}
    assert "HIDDEN_UNKNOWN" in _codes(report)


def test_hidden_conflict_blocks():
    ctx = _ctx(conflicts=[_conflict("CARGO_UN_MISMATCH")])
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].evidence_refs = ["block:ROAD-A"]
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "CONFLICT_HIDDEN" in _codes(report)


# --------------------------------------------------------------------------- #
# 12-14. forbidden claims / raw score / percentage
# --------------------------------------------------------------------------- #
def test_forbidden_claim_blocks():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.overall_summary = "The shipment is approved and customs cleared."
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "FORBIDDEN_CLAIM" in _codes(report)


def test_raw_score_text_blocks():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].why_ranked_here = "It has a raw_score that is high."
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "RAW_SCORE_LEAKAGE" in _codes(report)


def test_percentage_text_blocks():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.overall_summary = "Internal estimate around 80% done."
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "RAW_SCORE_LEAKAGE" in _codes(report)


# --------------------------------------------------------------------------- #
# 15. dispute signal
# --------------------------------------------------------------------------- #
def test_disputes_ranking_revises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.disputes_ranking = True
    draft.dispute_reason = "Evidence does not justify this ordering."
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.revise
    assert "ANALYST_DISPUTES_RANKING" in _codes(report)


# --------------------------------------------------------------------------- #
# 16. HIGH readiness inconsistency (manual decision with overall HIGH)
# --------------------------------------------------------------------------- #
def test_inconsistent_high_readiness_blocks():
    ctx = _ctx(hard_gates=[_gate("GB", RequestedMode.road, "blocking", "triggered")])
    # deliberately inconsistent decision: HIGH despite the triggered blocking gate
    decision = DeterministicDecision(
        case_id="case-1",
        overall_readiness_band=ReadinessBand.HIGH,
        ranking_type=RankingType.preparation_ranking,
        ranked_path_families=[
            RankedPathFamilyDecision(
                rank=1,
                path_family="road_preparation",
                mode=RequestedMode.road,
                readiness_band=ReadinessBand.HIGH,
                ranking_type=RankingType.preparation_ranking,
                evidence_refs=["block:ROAD-A", "gate:ROAD-A:GB"],
            )
        ],
        confidence_report=ConfidenceReport(band=ConfidenceBand.HIGH),
    )
    # surface the gate so HIDDEN_HARD_GATE does not fire; isolate the HIGH check
    draft = AnalystDraft(
        case_id="case-1",
        narratives=[
            AnalystPathNarrative(
                path_family="road_preparation",
                mode=RequestedMode.road,
                rank=1,
                why_ranked_here="Cited.",
                why_not_higher="Cited.",
                evidence_refs=["block:ROAD-A", "gate:ROAD-A:GB"],
            )
        ],
        overall_summary="Internal explanation.",
    )
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.block
    assert "INCONSISTENT_HIGH_READINESS" in _codes(report)


# --------------------------------------------------------------------------- #
# 17-18. not_triggered gate / can_wait do not force failure
# --------------------------------------------------------------------------- #
def test_not_triggered_gate_does_not_require_surfacing():
    ctx = _ctx(hard_gates=[_gate("GN", RequestedMode.road, "blocking", "not_triggered")])
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    draft.narratives[0].evidence_refs = ["block:ROAD-A"]  # gate not cited; allowed
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert report.status is SafetyGateStatus.pass_


def test_can_wait_missing_field_does_not_force_failure():
    ctx = _ctx(missing_fields=[_missing("packaging", "can_wait")])
    decision = _decision(ctx)
    report = run_safety_gate(context=ctx, decision=decision, analyst_draft=_draft_from_decision(decision))
    assert report.status is SafetyGateStatus.pass_


# --------------------------------------------------------------------------- #
# 19. no mutation
# --------------------------------------------------------------------------- #
def test_safety_gate_does_not_mutate_inputs():
    ctx = _ctx(
        hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")],
        conflicts=[_conflict("X")],
    )
    decision = _decision(ctx)
    draft = _draft_from_decision(decision)
    ctx_before, decision_before, draft_before = ctx.model_dump(), decision.model_dump(), draft.model_dump()
    run_safety_gate(context=ctx, decision=decision, analyst_draft=draft)
    assert ctx.model_dump() == ctx_before
    assert decision.model_dump() == decision_before
    assert draft.model_dump() == draft_before


# --------------------------------------------------------------------------- #
# 20. analyst_agent uses the shared safety_rules helpers
# --------------------------------------------------------------------------- #
def test_analyst_agent_uses_shared_safety_rules():
    source = inspect.getsource(analyst_agent)
    assert "from app.services.layer3.safety_rules import" in source
    assert hasattr(analyst_agent, "contains_forbidden_claim")
    assert hasattr(analyst_agent, "contains_raw_score_leakage")
    assert hasattr(analyst_agent, "allowed_evidence_refs")
