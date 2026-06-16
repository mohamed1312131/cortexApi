from __future__ import annotations

import inspect
import json

import pytest

from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    CriticFinding,
    CriticReview,
    CriticVerdict,
    DeterministicDecision,
    EvidenceRef,
    ReasoningContext,
    ReasoningFactor,
)
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.agents import critic_agent
from app.services.layer3.agents.critic_agent import (
    build_critic_prompt,
    build_critic_review,
)
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _block_ref(mode: RequestedMode) -> EvidenceRef:
    block_id = f"{mode.value.upper()}-A"
    return EvidenceRef(ref_id=f"block:{block_id}", source_type="block", source_block=block_id, mode=mode)


def _ctx() -> ReasoningContext:
    modes = [RequestedMode.road, RequestedMode.sea]
    return ReasoningContext(
        case_id="case-1",
        candidate_modes=modes,
        modes_covered=modes,
        active_profiles=[],
        block_statuses={"ROAD-A": "found", "SEA-A": "found"},
        hard_gates=[
            ReasoningFactor(
                code="G1", label="capacity", severity="high", mode=RequestedMode.road,
                evidence_refs=["gate:ROAD-A:G1"], status="triggered",
            )
        ],
        unknowns=[],
        missing_fields=[ReasoningFactor(code="incoterm", label="incoterm", severity="high_value", evidence_refs=["missing:incoterm"])],
        conflicts=[],
        confidence_cap_reasons=[],
        evidence_refs=[_block_ref(RequestedMode.road), _block_ref(RequestedMode.sea)],
        completeness_status="incomplete_but_usable",
    )


def _decision(context: ReasoningContext) -> DeterministicDecision:
    decision, _ = build_deterministic_decision(context, trace_id="t1")
    return decision


def _draft(decision: DeterministicDecision) -> AnalystDraft:
    narratives = [
        AnalystPathNarrative(
            path_family=p.path_family, mode=p.mode, rank=p.rank,
            why_ranked_here="Per cited evidence.", why_not_higher="Per cited caps.",
            what_would_improve_readiness=["Resolve cited gaps."],
            evidence_refs=list(p.evidence_refs),
        )
        for p in decision.ranked_path_families
    ]
    return AnalystDraft(case_id=decision.case_id, narratives=narratives, overall_summary="Internal readiness explanation.")


def _pass_review() -> CriticReview:
    return CriticReview(verdict=CriticVerdict.pass_)


def _revise_review() -> CriticReview:
    return CriticReview(
        verdict=CriticVerdict.revise,
        findings=[CriticFinding(code="VAGUE", severity="medium", message="Next action is vague.")],
        required_changes=["Make the next action concrete."],
    )


def _block_review() -> CriticReview:
    return CriticReview(
        verdict=CriticVerdict.block,
        findings=[CriticFinding(code="UNSAFE", severity="blocking", message="Analyst implies the shipment is approved.")],
        unsupported_claims=["shipment approved"],
        required_changes=["Remove the approval implication."],
    )


# --------------------------------------------------------------------------- #
# stub models
# --------------------------------------------------------------------------- #
class _StructuredRunnable:
    def __init__(self, parent: "_StructuredModel") -> None:
        self.parent = parent

    def invoke(self, prompt: str):
        self.parent.invoked_prompt = prompt
        return self.parent.result


class _StructuredModel:
    def __init__(self, result) -> None:
        self.result = result
        self.structured_called = False
        self.invoked_prompt: str | None = None

    def with_structured_output(self, schema):
        self.structured_called = True
        return _StructuredRunnable(self)


class _StructuredThenJsonRunnable:
    def __init__(self, parent: "_StructuredThenJsonModel") -> None:
        self.parent = parent

    def invoke(self, prompt: str):
        self.parent.structured_prompt = prompt
        return self.parent.structured_result


class _StructuredThenJsonModel:
    def __init__(self, *, structured_result, fallback_payload) -> None:
        self.structured_result = structured_result
        self.fallback_payload = fallback_payload
        self.structured_called = False
        self.fallback_called = False
        self.structured_prompt: str | None = None
        self.fallback_prompt: str | None = None

    def with_structured_output(self, schema):
        self.structured_called = True
        return _StructuredThenJsonRunnable(self)

    def invoke(self, prompt: str):
        self.fallback_called = True
        self.fallback_prompt = prompt
        text = (
            json.dumps(self.fallback_payload)
            if isinstance(self.fallback_payload, (dict, list))
            else self.fallback_payload
        )
        return _Msg(text)


class _Msg:
    def __init__(self, content) -> None:
        self.content = content


class _StructuredThenContentModel:
    def __init__(self, *, structured_result, fallback_content) -> None:
        self.structured_result = structured_result
        self.fallback_content = fallback_content
        self.structured_called = False
        self.fallback_called = False

    def with_structured_output(self, schema):
        self.structured_called = True
        return _StructuredThenJsonRunnable(self)

    def invoke(self, prompt: str):
        self.fallback_called = True
        return _Msg(self.fallback_content)


class _JsonModel:
    def __init__(self, payload) -> None:
        self.payload = payload

    def invoke(self, prompt: str):
        text = json.dumps(self.payload) if isinstance(self.payload, (dict, list)) else self.payload
        return _Msg(text)


class _FailingRunnable:
    def invoke(self, prompt: str):
        raise RuntimeError("provider unavailable")


class _FailingStructuredModel:
    def with_structured_output(self, schema):
        return _FailingRunnable()


def _build(review: CriticReview, *, model=None):
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    return build_critic_review(
        context=ctx, decision=decision, analyst_draft=draft,
        model=model or _StructuredModel(review),
    )


# --------------------------------------------------------------------------- #
# 1-3. happy path + paths
# --------------------------------------------------------------------------- #
def test_returns_critic_review():
    assert isinstance(_build(_pass_review()), CriticReview)


def test_json_fallback_parses_into_review():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    model = _JsonModel(_revise_review().model_dump(mode="json"))
    assert not hasattr(model, "with_structured_output")
    review = build_critic_review(context=ctx, decision=decision, analyst_draft=draft, model=model)
    assert isinstance(review, CriticReview)
    assert review.verdict is CriticVerdict.revise


def test_structured_output_path_used_when_supported():
    model = _StructuredModel(_pass_review())
    _build(_pass_review(), model=model)
    assert model.structured_called is True
    assert model.invoked_prompt is not None


def test_structured_output_none_uses_json_fallback():
    model = _StructuredThenJsonModel(
        structured_result=None,
        fallback_payload=_pass_review().model_dump(mode="json"),
    )
    review = _build(_pass_review(), model=model)
    assert review.verdict is CriticVerdict.pass_
    assert model.structured_called is True
    assert model.fallback_called is True


def test_structured_output_invalid_object_uses_json_fallback():
    model = _StructuredThenJsonModel(
        structured_result=object(),
        fallback_payload=_pass_review().model_dump(mode="json"),
    )
    review = _build(_pass_review(), model=model)
    assert review.verdict is CriticVerdict.pass_
    assert model.structured_called is True
    assert model.fallback_called is True


def test_structured_output_none_with_bad_json_fallback_raises_without_raw_output():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    model = _StructuredThenJsonModel(
        structured_result=None,
        fallback_payload="not json from provider",
    )

    with pytest.raises(ValueError, match="Critic returned non-JSON output") as excinfo:
        build_critic_review(context=ctx, decision=decision, analyst_draft=draft, model=model)

    assert model.fallback_called is True
    assert "not json from provider" not in str(excinfo.value)


def test_json_fallback_ignores_thought_part_and_parses_final_json():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    final_payload = _pass_review().model_dump(mode="json")
    thought_text = '{"verdict":"block","raw_score":99}'
    model = _StructuredThenContentModel(
        structured_result=None,
        fallback_content=[
            {"text": thought_text, "thought": True},
            {"text": json.dumps(final_payload)},
        ],
    )

    review = build_critic_review(context=ctx, decision=decision, analyst_draft=draft, model=model)

    assert review.verdict is CriticVerdict.pass_
    assert model.fallback_called is True


def test_json_fallback_thought_only_raises_sanitized_error():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    thought_text = '{"verdict":"block","raw_score":99}'
    model = _StructuredThenContentModel(
        structured_result=None,
        fallback_content=[{"text": thought_text, "thought": True}],
    )

    with pytest.raises(ValueError) as excinfo:
        build_critic_review(context=ctx, decision=decision, analyst_draft=draft, model=model)

    message = str(excinfo.value)
    assert "non-thought text" in message
    assert thought_text not in message
    assert "raw_score" not in message


# --------------------------------------------------------------------------- #
# 4-9. validation rules (mutate after construction to bypass schema)
# --------------------------------------------------------------------------- #
def test_revise_requires_findings():
    review = _revise_review()
    review.findings = []
    with pytest.raises(ValueError):
        _build(review)


def test_block_requires_findings():
    review = _block_review()
    review.findings = []
    with pytest.raises(ValueError):
        _build(review)


def test_contradiction_cannot_pass():
    review = _pass_review()
    review.contradiction_with_deterministic_ranking = True
    with pytest.raises(ValueError):
        _build(review)


def test_unsupported_claims_cannot_pass():
    review = _pass_review()
    review.unsupported_claims = ["unsupported statement"]
    with pytest.raises(ValueError):
        _build(review)


def test_forbidden_claim_in_unsupported_with_pass_fails():
    review = _pass_review()
    review.unsupported_claims = ["the shipment is approved"]
    with pytest.raises(ValueError):
        _build(review)


def test_revise_requires_required_changes():
    review = _revise_review()
    review.required_changes = []
    with pytest.raises(ValueError):
        _build(review)


# --------------------------------------------------------------------------- #
# 10. raw score leakage in findings/required_changes
# --------------------------------------------------------------------------- #
def test_raw_score_leakage_in_findings_raises():
    review = _revise_review()
    review.findings[0].message = "The raw_score should be exposed."
    with pytest.raises(ValueError):
        _build(review)


# --------------------------------------------------------------------------- #
# 11. provider failure -> controlled error
# --------------------------------------------------------------------------- #
def test_provider_failure_controlled():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    with pytest.raises((RuntimeError, ValueError)):
        build_critic_review(context=ctx, decision=decision, analyst_draft=draft, model=_FailingStructuredModel())


# --------------------------------------------------------------------------- #
# 12. no InternalScoringTrace usage
# --------------------------------------------------------------------------- #
def test_critic_does_not_use_internal_scoring_trace():
    source = inspect.getsource(critic_agent)
    assert "from app.schemas.internal_scoring_trace" not in source
    assert "import InternalScoringTrace" not in source
    assert not hasattr(critic_agent, "InternalScoringTrace")


# --------------------------------------------------------------------------- #
# 13. inputs not mutated
# --------------------------------------------------------------------------- #
def test_inputs_not_mutated():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _draft(decision)
    before = (ctx.model_dump(), decision.model_dump(), draft.model_dump())
    build_critic_review(context=ctx, decision=decision, analyst_draft=draft, model=_StructuredModel(_pass_review()))
    assert (ctx.model_dump(), decision.model_dump(), draft.model_dump()) == before


# --------------------------------------------------------------------------- #
# 14-15. prompt rules
# --------------------------------------------------------------------------- #
def test_prompt_includes_fixed_truth_rule():
    ctx = _ctx()
    decision = _decision(ctx)
    prompt = build_critic_prompt(ctx, decision, _draft(decision))
    assert "fixed truth" in prompt.lower()


def test_prompt_includes_no_final_customer_rule():
    ctx = _ctx()
    decision = _decision(ctx)
    prompt = build_critic_prompt(ctx, decision, _draft(decision))
    assert "final customer" in prompt.lower()


# --------------------------------------------------------------------------- #
# 16. no live LLM
# --------------------------------------------------------------------------- #
def test_no_live_llm_call(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("get_chat_model must not be called when a model is injected")

    monkeypatch.setattr(critic_agent, "get_chat_model", _boom)
    assert isinstance(_build(_pass_review()), CriticReview)


# --------------------------------------------------------------------------- #
# 17. shared safety_rules usage
# --------------------------------------------------------------------------- #
def test_uses_shared_safety_rules():
    source = inspect.getsource(critic_agent)
    assert "from app.services.layer3.safety_rules import" in source
    assert hasattr(critic_agent, "contains_forbidden_claim")
    assert hasattr(critic_agent, "contains_raw_score_leakage")


# --------------------------------------------------------------------------- #
# 18-20. accepted verdicts
# --------------------------------------------------------------------------- #
def test_pass_with_empty_findings_accepted():
    review = _build(_pass_review())
    assert review.verdict is CriticVerdict.pass_
    assert review.findings == []


def test_revise_with_concrete_finding_accepted():
    review = _build(_revise_review())
    assert review.verdict is CriticVerdict.revise
    assert review.findings
    assert review.required_changes


def test_block_with_forbidden_claim_finding_accepted():
    review = _build(_block_review())
    assert review.verdict is CriticVerdict.block
    assert review.findings
