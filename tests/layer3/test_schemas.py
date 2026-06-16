from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import Layer3Result, Layer3Status, ReasoningDecision
from app.schemas import reasoning_decision as reasoning_decision_module
from app.schemas.internal_scoring_trace import InternalScoringTrace, ScoringStep
from app.schemas.layer3 import (
    AnalystDraft,
    CriticFinding,
    CriticReview,
    CriticVerdict,
    DeterministicDecision,
    Layer3NextAction,
    RankedPathFamilyDecision,
    ReasoningContext,
    ReasoningFactor,
    SafetyGateReport,
    SafetyGateStatus,
)
from app.schemas.reasoning_decision import (
    ConfidenceBand,
    ConfidenceReport,
    RankingType,
    ReadinessBand,
)
from app.schemas.shipment_request import RequestedMode


def _confidence() -> ConfidenceReport:
    return ConfidenceReport(band=ConfidenceBand.MEDIUM)


def _ranked_family(**overrides) -> RankedPathFamilyDecision:
    defaults = dict(
        rank=1,
        path_family="sea_standard",
        mode=RequestedMode.sea,
        readiness_band=ReadinessBand.MEDIUM,
        ranking_type=RankingType.preparation_ranking,
        evidence_refs=["ev1"],
    )
    defaults.update(overrides)
    return RankedPathFamilyDecision(**defaults)


# ---- ReasoningFactor optional status ----
def test_reasoning_factor_status_optional():
    default = ReasoningFactor(code="c", label="l", severity="blocking")
    assert default.status is None

    triggered = ReasoningFactor(
        code="c", label="l", severity="blocking", status="triggered"
    )
    assert triggered.status == "triggered"


# ---- ReasoningContext mode hygiene ----
def test_reasoning_context_rejects_unknown_candidate_mode():
    with pytest.raises(ValidationError):
        ReasoningContext(
            case_id="c1",
            candidate_modes=[RequestedMode.sea, RequestedMode.unknown],
        )


def test_reasoning_context_rejects_unknown_modes_covered():
    with pytest.raises(ValidationError):
        ReasoningContext(
            case_id="c1",
            modes_covered=[RequestedMode.unknown],
        )


def test_reasoning_context_accepts_concrete_modes():
    ctx = ReasoningContext(
        case_id="c1",
        candidate_modes=[RequestedMode.sea, RequestedMode.air],
        modes_covered=[RequestedMode.sea],
    )
    assert ctx.candidate_modes == [RequestedMode.sea, RequestedMode.air]


# ---- RankedPathFamilyDecision validators ----
def test_ranked_path_family_rejects_rank_below_one():
    with pytest.raises(ValidationError):
        _ranked_family(rank=0)


def test_ranked_path_family_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        _ranked_family(mode=RequestedMode.unknown)


def test_ranked_path_family_requires_evidence_refs():
    with pytest.raises(ValidationError):
        _ranked_family(evidence_refs=[])


# ---- DeterministicDecision: no raw score leakage ----
def test_deterministic_decision_has_no_raw_score_fields():
    forbidden = {"raw_score", "raw_scores", "raw_scores_by_path", "score", "scores"}
    assert forbidden.isdisjoint(DeterministicDecision.model_fields.keys())


def test_deterministic_decision_builds_and_references_trace_by_id_only():
    decision = DeterministicDecision(
        case_id="c1",
        overall_readiness_band=ReadinessBand.MEDIUM,
        ranking_type=RankingType.preparation_ranking,
        ranked_path_families=[_ranked_family()],
        confidence_report=_confidence(),
        internal_trace_ref="trace-123",
    )
    assert decision.internal_trace_ref == "trace-123"
    # The trace reference is a plain string id, not an embedded trace object.
    assert isinstance(decision.internal_trace_ref, str)


def test_deterministic_decision_requires_families_unless_blocked():
    with pytest.raises(ValidationError):
        DeterministicDecision(
            case_id="c1",
            overall_readiness_band=ReadinessBand.MEDIUM,
            ranking_type=RankingType.preparation_ranking,
            ranked_path_families=[],
            confidence_report=_confidence(),
        )
    # BLOCKED may legitimately have no ranked families.
    blocked = DeterministicDecision(
        case_id="c1",
        overall_readiness_band=ReadinessBand.BLOCKED,
        ranking_type=RankingType.blocked_ranking,
        ranked_path_families=[],
        confidence_report=_confidence(),
    )
    assert blocked.ranked_path_families == []


# ---- InternalScoringTrace holds raw scores, separate from DeterministicDecision ----
def test_internal_scoring_trace_holds_raw_scores():
    trace = InternalScoringTrace(
        case_id="c1",
        reasoning_decision_id="rd-1",
        steps=[
            ScoringStep(
                step_name="base_score",
                mode=RequestedMode.sea,
                raw_score=0.82,
                resulting_band=ReadinessBand.MEDIUM,
                reason="baseline",
            )
        ],
        raw_scores_by_path={"sea_standard": 0.82},
    )
    assert trace.raw_scores_by_path["sea_standard"] == 0.82
    assert trace.steps[0].raw_score == 0.82
    # raw scores live here and NOT on DeterministicDecision
    assert "raw_scores_by_path" in InternalScoringTrace.model_fields
    assert "raw_scores_by_path" not in DeterministicDecision.model_fields


# ---- AnalystDraft dispute consistency ----
def test_analyst_draft_dispute_requires_reason():
    with pytest.raises(ValidationError):
        AnalystDraft(
            case_id="c1",
            overall_summary="summary",
            disputes_ranking=True,
        )


def test_analyst_draft_dispute_with_reason_ok():
    draft = AnalystDraft(
        case_id="c1",
        overall_summary="summary",
        disputes_ranking=True,
        dispute_reason="deterministic ranking not explainable from evidence",
    )
    assert draft.disputes_ranking is True


# ---- CriticReview contradiction consistency ----
def test_critic_review_contradiction_cannot_pass():
    with pytest.raises(ValidationError):
        CriticReview(
            verdict=CriticVerdict.pass_,
            contradiction_with_deterministic_ranking=True,
        )


def test_critic_review_contradiction_block_ok():
    review = CriticReview(
        verdict=CriticVerdict.block,
        findings=[CriticFinding(code="CONTRADICTION", severity="high", message="reorders ranking")],
        contradiction_with_deterministic_ranking=True,
    )
    assert review.verdict is CriticVerdict.block


def test_critic_review_revise_requires_findings():
    with pytest.raises(ValidationError):
        CriticReview(verdict=CriticVerdict.revise, findings=[])


# ---- SafetyGateReport status/pass consistency ----
def test_safety_gate_pass_consistent():
    report = SafetyGateReport(
        status=SafetyGateStatus.pass_,
        passed=True,
        next_action=Layer3NextAction.pass_to_layer4,
    )
    assert report.passed is True


def test_safety_gate_pass_requires_passed_true():
    with pytest.raises(ValidationError):
        SafetyGateReport(
            status=SafetyGateStatus.pass_,
            passed=False,
            next_action=Layer3NextAction.pass_to_layer4,
        )


def test_safety_gate_block_requires_passed_false():
    with pytest.raises(ValidationError):
        SafetyGateReport(
            status=SafetyGateStatus.block,
            passed=True,
            next_action=Layer3NextAction.block_unsafe,
        )


def test_safety_gate_block_next_action_alignment():
    with pytest.raises(ValidationError):
        SafetyGateReport(
            status=SafetyGateStatus.block,
            passed=False,
            next_action=Layer3NextAction.pass_to_layer4,
        )


# ---- Layer3Result status consistency ----
def test_layer3_result_pass_requires_reasoning_decision():
    with pytest.raises(ValidationError):
        Layer3Result(case_id="c1", status=Layer3Status.pass_to_layer4)


def test_layer3_result_pass_with_decision_ok():
    decision = ReasoningDecision(
        case_id="c1",
        reasoning_decision_id="rd-1",
        ranking_type=RankingType.preparation_ranking,
        confidence=_confidence(),
    )
    result = Layer3Result(
        case_id="c1",
        status=Layer3Status.pass_to_layer4,
        reasoning_decision=decision,
    )
    assert result.reasoning_decision is decision


def test_layer3_result_clarification_allows_no_decision():
    result = Layer3Result(case_id="c1", status=Layer3Status.request_user_clarification)
    assert result.reasoning_decision is None


# ---- ReasoningDecision is reused, not redefined ----
def test_reasoning_decision_not_redefined():
    # The public export and the module-level class are the same object.
    assert ReasoningDecision is reasoning_decision_module.ReasoningDecision
