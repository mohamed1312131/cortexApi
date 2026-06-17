from __future__ import annotations

import pytest

from app.schemas.block_response import HardGate, Unknown
from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    CriticFinding,
    CriticReview,
    CriticVerdict,
    EvidenceRef,
    Layer3NextAction,
    ReasoningContext,
    ReasoningFactor,
    SafetyGateReport,
    SafetyGateStatus,
)
from app.schemas.reasoning_decision import ConfidenceBand, ReasoningDecision
from app.schemas import reasoning_decision as reasoning_decision_module
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.decision_builder import build_reasoning_decision
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _block_ref(mode: RequestedMode) -> EvidenceRef:
    block_id = f"{mode.value.upper()}-A"
    return EvidenceRef(ref_id=f"block:{block_id}", source_type="block", source_block=block_id, mode=mode)


def _readiness_option(result, mode: RequestedMode):
    for option in result.ranked_readiness_options:
        if option.mode is mode:
            return option
    raise AssertionError(f"Expected readiness option for {mode.value}")


def _ctx(
    *,
    modes=None,
    active_profiles=None,
    hard_gates=None,
    unknowns=None,
    missing_fields=None,
    conflicts=None,
    completeness_status="incomplete_but_usable",
) -> ReasoningContext:
    modes = modes or [RequestedMode.road, RequestedMode.sea]
    return ReasoningContext(
        case_id="case-1",
        candidate_modes=modes,
        modes_covered=modes,
        active_profiles=active_profiles or [],
        block_statuses={f"{m.value.upper()}-A": "found" for m in modes},
        hard_gates=hard_gates or [],
        unknowns=unknowns or [],
        missing_fields=missing_fields or [ReasoningFactor(code="incoterm", label="incoterm", severity="high_value", evidence_refs=["missing:incoterm"])],
        conflicts=conflicts or [],
        confidence_cap_reasons=[],
        evidence_refs=[_block_ref(m) for m in modes],
        completeness_status=completeness_status,
    )


def _gate(code, mode, severity, status) -> ReasoningFactor:
    src = f"{mode.value.upper()}-A"
    return ReasoningFactor(
        code=code, label=f"{code} message", severity=severity, mode=mode,
        evidence_refs=[f"gate:{src}:{code}"], status=status,
        details=f"status={status} | source_block={src} | basis=rulebook",
    )


def _unknown(code, *, mode=None, severity="unknown") -> ReasoningFactor:
    src = f"{mode.value.upper()}-A" if mode else "global"
    return ReasoningFactor(code=code, label=f"{code} unknown", severity=severity, mode=mode, evidence_refs=[f"unknown:{src}:{code}"], details="cannot assess")


def _decision(ctx):
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    return decision


def _draft(decision, *, disputes=False) -> AnalystDraft:
    narratives = [
        AnalystPathNarrative(
            path_family=p.path_family, mode=p.mode, rank=p.rank,
            why_ranked_here="Ranked per cited evidence.", why_not_higher="Held below top per cited caps.",
            what_would_improve_readiness=["Resolve the cited gaps."],
            evidence_refs=list(p.evidence_refs),
        )
        for p in decision.ranked_path_families
    ]
    return AnalystDraft(
        case_id=decision.case_id, narratives=narratives,
        overall_summary="Internal readiness explanation.",
        disputes_ranking=disputes, dispute_reason="x" if disputes else None,
    )


def _gate_pass() -> SafetyGateReport:
    return SafetyGateReport(status=SafetyGateStatus.pass_, passed=True, next_action=Layer3NextAction.pass_to_layer4)


def _gate_fail() -> SafetyGateReport:
    return SafetyGateReport(status=SafetyGateStatus.block, passed=False, next_action=Layer3NextAction.block_unsafe)


def _build(ctx, decision, draft, *, gate=None, critic=None) -> ReasoningDecision:
    return build_reasoning_decision(
        context=ctx, decision=decision, analyst_draft=draft,
        safety_gate_report=gate or _gate_pass(), critic_review=critic,
    )


# --------------------------------------------------------------------------- #
# 1. happy path
# --------------------------------------------------------------------------- #
def test_returns_reasoning_decision():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    assert isinstance(out, ReasoningDecision)
    assert out.case_id == "case-1"
    assert len(out.ranked_readiness_options) == len(decision.ranked_path_families)


# --------------------------------------------------------------------------- #
# 2-4. preconditions
# --------------------------------------------------------------------------- #
def test_gate_not_passed_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    with pytest.raises(ValueError):
        _build(ctx, decision, _draft(decision), gate=_gate_fail())


def test_critic_revise_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    critic = CriticReview(verdict=CriticVerdict.revise, findings=[CriticFinding(code="C", severity="medium", message="m")], required_changes=["fix"])
    with pytest.raises(ValueError):
        _build(ctx, decision, _draft(decision), critic=critic)


def test_critic_block_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    critic = CriticReview(verdict=CriticVerdict.block, findings=[CriticFinding(code="C", severity="blocking", message="m")])
    with pytest.raises(ValueError):
        _build(ctx, decision, _draft(decision), critic=critic)


def test_critic_pass_and_skipped_allowed():
    ctx = _ctx()
    decision = _decision(ctx)
    assert _build(ctx, decision, _draft(decision), critic=CriticReview(verdict=CriticVerdict.pass_))
    assert _build(ctx, decision, _draft(decision), critic=CriticReview(verdict=CriticVerdict.skipped))


# --------------------------------------------------------------------------- #
# 5-7. ranking fidelity + narratives
# --------------------------------------------------------------------------- #
def test_ranked_options_match_decision():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    assert [(o.rank, o.mode, o.path_family_id) for o in out.ranked_readiness_options] == [
        (p.rank, p.mode, p.path_family) for p in decision.ranked_path_families
    ]


def test_analyst_cannot_change_band_or_order():
    ctx = _ctx(hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")])
    decision = _decision(ctx)
    draft = _draft(decision)
    # tamper: analyst narrative tries to claim a different ordering is irrelevant —
    # bands/order come from the decision, so the output must match the decision.
    out = _build(ctx, decision, draft)
    for option, path in zip(out.ranked_readiness_options, decision.ranked_path_families):
        assert option.readiness_band is path.readiness_band
        assert option.rank == path.rank
        assert option.mode is path.mode


def test_every_option_has_why_fields():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    for option in out.ranked_readiness_options:
        assert option.why_ranked_here.strip()
        assert option.why_not_higher.strip()


# --------------------------------------------------------------------------- #
# 8. visibility of gates/unknowns/missing/conflicts
# --------------------------------------------------------------------------- #
def test_global_factors_remain_visible():
    ctx = _ctx(
        hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")],
        unknowns=[_unknown("classification", mode=RequestedMode.road, severity="high")],
        conflicts=[ReasoningFactor(code="CARGO_UN", label="conflict", severity="conflict", evidence_refs=["conflict:0:CARGO_UN"])],
        missing_fields=[ReasoningFactor(code="weight", label="weight", severity="blocking", evidence_refs=["missing:weight"])],
    )
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    # unknowns visible at top
    assert any(u.field == "classification" for u in out.global_unknowns)
    # hard gate visible on the road option (typed HardGate)
    road = _readiness_option(out, RequestedMode.road)
    assert any(isinstance(g, HardGate) and g.gate_id == "G1" for g in road.hard_gates)
    # missing field + conflict surfaced in next actions
    assert any("weight" in a for a in out.global_next_actions)
    assert any("CARGO_UN" in a for a in out.global_next_actions)
    # conflict + gate warnings carried
    codes = {w.code for w in out.must_show_warnings}
    assert "CONFLICT_PRESENT" in codes
    assert "BLOCKING_HARD_GATE" in codes or "CRITICAL_UNKNOWN" in codes


# --------------------------------------------------------------------------- #
# 9. warnings carried + deduped
# --------------------------------------------------------------------------- #
def test_must_show_warnings_deduped():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    keys = [(w.code, w.message) for w in out.must_show_warnings]
    assert len(keys) == len(set(keys))
    assert any(w.code == "NOT_FINAL_APPROVAL" for w in out.must_show_warnings)


# --------------------------------------------------------------------------- #
# 10. confidence banded only
# --------------------------------------------------------------------------- #
def test_confidence_is_banded_no_floats():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    assert isinstance(out.confidence.band, ConfidenceBand)
    assert "value" not in out.confidence.model_dump()
    assert all(isinstance(r, str) for r in out.confidence.cap_reasons)


# --------------------------------------------------------------------------- #
# 11. no raw scores in the dump
# --------------------------------------------------------------------------- #
def test_no_raw_scores_in_dump():
    ctx = _ctx(hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")])
    decision = _decision(ctx)
    dumped = str(_build(ctx, decision, _draft(decision)).model_dump())
    assert "raw_score" not in dumped
    assert "raw_scores_by_path" not in dumped
    assert "internal_scoring_trace" not in dumped


# --------------------------------------------------------------------------- #
# 12. forbidden claims only in forbidden_claims
# --------------------------------------------------------------------------- #
def test_forbidden_claims_only_in_forbidden_list():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    assert out.forbidden_claims  # populated
    assert "approved" in out.forbidden_claims
    # none of the forbidden phrases appear in allowed_claims
    joined_allowed = " ".join(out.allowed_claims).lower()
    for phrase in out.forbidden_claims:
        assert phrase not in joined_allowed


# --------------------------------------------------------------------------- #
# 13. no unknown mode
# --------------------------------------------------------------------------- #
def test_no_unknown_mode_in_options():
    ctx = _ctx(modes=[RequestedMode.road, RequestedMode.sea, RequestedMode.air])
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    assert all(o.mode is not RequestedMode.unknown for o in out.ranked_readiness_options)


# --------------------------------------------------------------------------- #
# 14. determinism
# --------------------------------------------------------------------------- #
def test_deterministic_output():
    ctx = _ctx(hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")])
    decision = _decision(ctx)
    a = _build(ctx, decision, _draft(decision)).model_dump()
    b = _build(ctx, decision, _draft(decision)).model_dump()
    assert a == b


# --------------------------------------------------------------------------- #
# 15. no mutation
# --------------------------------------------------------------------------- #
def test_inputs_not_mutated():
    ctx = _ctx(hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")])
    decision = _decision(ctx)
    draft = _draft(decision)
    before = (ctx.model_dump(), decision.model_dump(), draft.model_dump())
    _build(ctx, decision, draft)
    assert (ctx.model_dump(), decision.model_dump(), draft.model_dump()) == before


# --------------------------------------------------------------------------- #
# 16. ReasoningDecision reused, not redefined
# --------------------------------------------------------------------------- #
def test_reasoning_decision_not_redefined():
    assert ReasoningDecision is reasoning_decision_module.ReasoningDecision


# --------------------------------------------------------------------------- #
# 17. missing narrative -> ValueError
# --------------------------------------------------------------------------- #
def test_missing_narrative_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    draft.narratives.pop()  # drop one path's narrative
    with pytest.raises(ValueError):
        _build(ctx, decision, draft)


# --------------------------------------------------------------------------- #
# 18. evidence_refs: the frozen seam has no such field -> not present
# --------------------------------------------------------------------------- #
def test_seam_has_no_evidence_refs_field():
    ctx = _ctx()
    decision = _decision(ctx)
    out = _build(ctx, decision, _draft(decision))
    assert "evidence_refs" not in out.model_fields
    for option in out.ranked_readiness_options:
        assert "evidence_refs" not in option.model_fields
    # typed seam objects are produced for gates/unknowns
    assert all(isinstance(u, Unknown) for u in out.global_unknowns)
