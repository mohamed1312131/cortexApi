from __future__ import annotations

import inspect
import json

import pytest

from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    DeterministicDecision,
    EvidenceRef,
    ReasoningContext,
    ReasoningFactor,
)
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.agents import analyst_agent
from app.services.layer3.agents.analyst_agent import build_analyst_draft, build_analyst_prompt
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision


# --------------------------------------------------------------------------- #
# fixtures: a realistic (context, decision) from the deterministic engine
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
                code="G1",
                label="capacity",
                severity="high",
                mode=RequestedMode.road,
                evidence_refs=["gate:ROAD-A:G1"],
                status="triggered",
            )
        ],
        unknowns=[],
        missing_fields=[
            ReasoningFactor(code="incoterm", label="incoterm", severity="high_value", evidence_refs=["missing:incoterm"])
        ],
        conflicts=[],
        confidence_cap_reasons=[],
        evidence_refs=[_block_ref(RequestedMode.road), _block_ref(RequestedMode.sea)],
        completeness_status="incomplete_but_usable",
    )


def _ctx_three_modes() -> ReasoningContext:
    modes = [RequestedMode.sea, RequestedMode.air, RequestedMode.road]
    return ReasoningContext(
        case_id="case-1",
        candidate_modes=modes,
        modes_covered=modes,
        active_profiles=[],
        block_statuses={"SEA-A": "found", "AIR-A": "found", "ROAD-A": "found"},
        hard_gates=[],
        unknowns=[],
        missing_fields=[
            ReasoningFactor(code="incoterm", label="incoterm", severity="high_value", evidence_refs=["missing:incoterm"])
        ],
        conflicts=[],
        confidence_cap_reasons=[],
        evidence_refs=[_block_ref(mode) for mode in modes],
        completeness_status="incomplete_but_usable",
    )


def _decision(context: ReasoningContext) -> DeterministicDecision:
    decision, _ = build_deterministic_decision(context, trace_id="t1")
    return decision


def _valid_draft(decision: DeterministicDecision) -> AnalystDraft:
    narratives = [
        AnalystPathNarrative(
            path_family=p.path_family,
            mode=p.mode,
            rank=p.rank,
            why_ranked_here="Ranked here based on the cited evidence and applied caps.",
            why_not_higher="Held below the top band by the cited gates and missing fields.",
            what_would_improve_readiness=["Resolve the missing field listed in the evidence."],
            evidence_refs=list(p.evidence_refs),
        )
        for p in decision.ranked_path_families
    ]
    return AnalystDraft(
        case_id=decision.case_id,
        narratives=narratives,
        overall_summary="Internal explanation of the deterministic readiness result.",
        next_action_summary="Resolve the listed gaps to improve readiness.",
    )


# --------------------------------------------------------------------------- #
# stub models (no live LLM)
# --------------------------------------------------------------------------- #
class _StructuredRunnable:
    def __init__(self, parent: "_StructuredModel") -> None:
        self.parent = parent

    def invoke(self, prompt: str):
        self.parent.invoked_prompt = prompt
        return self.parent.result


class _StructuredModel:
    """Model exposing with_structured_output, returning a preset AnalystDraft."""

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
    """Model WITHOUT structured output; returns a JSON message (fallback path)."""

    def __init__(self, payload) -> None:
        self.payload = payload

    def invoke(self, prompt: str):
        text = json.dumps(self.payload) if isinstance(self.payload, (dict, list)) else self.payload
        return _Msg(text)


class _FailingRunnable:
    @staticmethod
    def invoke(prompt: str):
        raise RuntimeError("provider unavailable")


class _FailingStructuredModel:
    @staticmethod
    def with_structured_output(schema):
        return _FailingRunnable()


# --------------------------------------------------------------------------- #
# 1 + 2. happy path
# --------------------------------------------------------------------------- #
def test_build_analyst_draft_returns_draft():
    ctx = _ctx()
    decision = _decision(ctx)
    model = _StructuredModel(_valid_draft(decision))
    draft = build_analyst_draft(context=ctx, decision=decision, model=model)
    assert isinstance(draft, AnalystDraft)


def test_prompt_includes_ranked_path_evidence_refs_and_revision_feedback():
    ctx = _ctx_three_modes()
    decision = _decision(ctx)

    prompt = build_analyst_prompt(
        ctx,
        decision,
        revision_feedback="Analyst narrative for rank 1 has no evidence_refs.",
    )

    assert "<ranked_path_evidence_refs>" in prompt
    assert '"rank": 1' in prompt
    assert '"evidence_refs":' in prompt
    assert "copy the evidence_refs from the matching ranked path" in prompt
    assert "rank + mode + path_family" in prompt
    assert "Do not leave evidence_refs empty" in prompt
    assert "<required_narratives>" in prompt
    assert "The number of narratives must equal the number of required_narratives" in prompt
    assert "Do not output only the best-ranked path" in prompt
    required_section = prompt.split("<required_narratives>", 1)[1].split("</required_narratives>", 1)[0]
    for path in decision.ranked_path_families:
        assert f'"rank": {path.rank}' in required_section
        assert f'"mode": "{path.mode.value}"' in required_section
        assert f'"path_family": "{path.path_family}"' in required_section
        assert f'"readiness_band": "{path.readiness_band.value}"' in required_section
        assert '"evidence_refs":' in required_section
    assert "<revision_feedback>" in prompt
    assert "Previous output failed validation: Analyst narrative for rank 1 has no evidence_refs." in prompt


def test_one_narrative_per_ranked_path():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(_valid_draft(decision)))
    assert len(draft.narratives) == len(decision.ranked_path_families)
    assert [n.rank for n in draft.narratives] == [p.rank for p in decision.ranked_path_families]


# --------------------------------------------------------------------------- #
# 3-6. cannot re-rank / add / omit / invent refs
# --------------------------------------------------------------------------- #
def test_cannot_silently_rerank():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives[0].rank = 99  # tamper with rank
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_cannot_add_extra_narrative():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives.append(
        AnalystPathNarrative(
            path_family="air_preparation",
            mode=RequestedMode.air,
            rank=99,
            why_ranked_here="x",
            why_not_higher="y",
            evidence_refs=["block:AIR-A"],
        )
    )
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_cannot_omit_narrative():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives.pop()
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_partial_rank_one_draft_identifies_missing_required_narratives():
    ctx = _ctx_three_modes()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives = [n for n in draft.narratives if n.rank == 1]

    with pytest.raises(ValueError) as excinfo:
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))

    message = str(excinfo.value)
    assert "Analyst omitted required narratives:" in message
    assert "<RequestedMode" not in message
    assert "(2," not in message
    for path in decision.ranked_path_families:
        if path.rank == 1:
            continue
        assert f"rank {path.rank} / {path.mode.value} / {path.path_family}" in message


def test_evidence_refs_must_be_subset():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives[0].evidence_refs = ["block:FAKE-XYZ"]
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_empty_evidence_refs_rejected():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives[0].evidence_refs = []
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_analyst_path_narrative_requires_non_empty_evidence_refs():
    with pytest.raises(Exception):
        AnalystPathNarrative(
            path_family="road_preparation",
            mode=RequestedMode.road,
            rank=1,
            why_ranked_here="x",
            why_not_higher="y",
            evidence_refs=[],
        )


def test_structured_output_missing_evidence_refs_rejected_clearly():
    ctx = _ctx()
    decision = _decision(ctx)
    payload = _valid_draft(decision).model_dump(mode="json")
    payload["narratives"][0].pop("evidence_refs")

    with pytest.raises(ValueError, match="Analyst structured output did not match AnalystDraft"):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(payload))


def test_structured_output_none_uses_json_fallback():
    ctx = _ctx()
    decision = _decision(ctx)
    fallback_payload = _valid_draft(decision).model_dump(mode="json")
    model = _StructuredThenJsonModel(
        structured_result=None,
        fallback_payload=fallback_payload,
    )

    draft = build_analyst_draft(context=ctx, decision=decision, model=model)

    assert isinstance(draft, AnalystDraft)
    assert model.structured_called is True
    assert model.fallback_called is True


def test_structured_output_invalid_object_uses_json_fallback():
    ctx = _ctx()
    decision = _decision(ctx)
    fallback_payload = _valid_draft(decision).model_dump(mode="json")
    model = _StructuredThenJsonModel(
        structured_result=object(),
        fallback_payload=fallback_payload,
    )

    draft = build_analyst_draft(context=ctx, decision=decision, model=model)

    assert isinstance(draft, AnalystDraft)
    assert model.structured_called is True
    assert model.fallback_called is True


def test_structured_output_none_with_bad_json_fallback_raises_without_raw_output():
    ctx = _ctx()
    decision = _decision(ctx)
    model = _StructuredThenJsonModel(
        structured_result=None,
        fallback_payload="not json from provider",
    )

    with pytest.raises(ValueError, match="Analyst returned non-JSON output") as excinfo:
        build_analyst_draft(context=ctx, decision=decision, model=model)

    assert model.fallback_called is True
    assert "not json from provider" not in str(excinfo.value)


def test_json_fallback_ignores_thought_part_and_parses_final_json():
    ctx = _ctx()
    decision = _decision(ctx)
    final_payload = _valid_draft(decision).model_dump(mode="json")
    thought_text = '{"case_id":"thought","raw_score":99}'
    model = _StructuredThenContentModel(
        structured_result=None,
        fallback_content=[
            {"text": thought_text, "thought": True},
            {"text": json.dumps(final_payload)},
        ],
    )

    draft = build_analyst_draft(context=ctx, decision=decision, model=model)

    assert isinstance(draft, AnalystDraft)
    assert draft.case_id == decision.case_id
    assert model.fallback_called is True


def test_json_fallback_thought_only_raises_sanitized_error():
    ctx = _ctx()
    decision = _decision(ctx)
    thought_text = '{"case_id":"thought","raw_score":99}'
    model = _StructuredThenContentModel(
        structured_result=None,
        fallback_content=[{"text": thought_text, "thought": True}],
    )

    with pytest.raises(ValueError) as excinfo:
        build_analyst_draft(context=ctx, decision=decision, model=model)

    message = str(excinfo.value)
    assert "non-thought text" in message
    assert thought_text not in message
    assert "raw_score" not in message


# --------------------------------------------------------------------------- #
# 7. disputes_ranking
# --------------------------------------------------------------------------- #
def test_disputes_ranking_with_reason_is_allowed():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.disputes_ranking = True
    draft.dispute_reason = "Evidence does not clearly justify this ordering."
    result = build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))
    # ranking is unchanged even when disputed
    assert [n.rank for n in result.narratives] == [p.rank for p in decision.ranked_path_families]


def test_disputes_ranking_without_reason_is_rejected_by_schema():
    ctx = _ctx()
    decision = _decision(ctx)
    base = _valid_draft(decision)
    with pytest.raises(Exception):
        AnalystDraft(
            case_id=base.case_id,
            narratives=base.narratives,
            overall_summary=base.overall_summary,
            disputes_ranking=True,
        )


# --------------------------------------------------------------------------- #
# 8 + 9. forbidden claims / raw score leakage
# --------------------------------------------------------------------------- #
def test_forbidden_claim_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.overall_summary = "This shipment is approved and customs cleared."
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_raw_score_leakage_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.narratives[0].why_ranked_here = "It has a raw_score of high value."
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


def test_percentage_leakage_raises():
    ctx = _ctx()
    decision = _decision(ctx)
    draft = _valid_draft(decision)
    draft.overall_summary = "Readiness is about 87% complete."
    with pytest.raises(ValueError):
        build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(draft))


# --------------------------------------------------------------------------- #
# 10. no InternalScoringTrace usage
# --------------------------------------------------------------------------- #
def test_analyst_agent_does_not_use_internal_scoring_trace():
    source = inspect.getsource(analyst_agent)
    # not imported and not referenced as a symbol (the leakage-detection token
    # string is allowed; what matters is that the model/module is never used).
    assert "from app.schemas.internal_scoring_trace" not in source
    assert "import InternalScoringTrace" not in source
    assert not hasattr(analyst_agent, "InternalScoringTrace")


# --------------------------------------------------------------------------- #
# 11 + 12. JSON fallback vs structured output paths
# --------------------------------------------------------------------------- #
def test_json_fallback_parses_into_draft():
    ctx = _ctx()
    decision = _decision(ctx)
    payload = _valid_draft(decision).model_dump(mode="json")
    model = _JsonModel(payload)
    assert not hasattr(model, "with_structured_output")
    draft = build_analyst_draft(context=ctx, decision=decision, model=model)
    assert isinstance(draft, AnalystDraft)
    assert len(draft.narratives) == len(decision.ranked_path_families)


def test_structured_output_path_is_used_when_supported():
    ctx = _ctx()
    decision = _decision(ctx)
    model = _StructuredModel(_valid_draft(decision))
    build_analyst_draft(context=ctx, decision=decision, model=model)
    assert model.structured_called is True
    assert model.invoked_prompt is not None


# --------------------------------------------------------------------------- #
# 13. provider failure -> controlled error
# --------------------------------------------------------------------------- #
def test_provider_failure_raises_controlled_error():
    ctx = _ctx()
    decision = _decision(ctx)
    with pytest.raises((RuntimeError, ValueError)):
        build_analyst_draft(context=ctx, decision=decision, model=_FailingStructuredModel())


# --------------------------------------------------------------------------- #
# 14. inputs are not mutated
# --------------------------------------------------------------------------- #
def test_inputs_not_mutated():
    ctx = _ctx()
    decision = _decision(ctx)
    ctx_before = ctx.model_dump()
    decision_before = decision.model_dump()
    build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(_valid_draft(decision)))
    assert ctx.model_dump() == ctx_before
    assert decision.model_dump() == decision_before


# --------------------------------------------------------------------------- #
# 15. no live LLM: injected model means the factory is never called
# --------------------------------------------------------------------------- #
def test_no_live_llm_call(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("get_chat_model must not be called when a model is injected")

    monkeypatch.setattr(analyst_agent, "get_chat_model", _boom)
    ctx = _ctx()
    decision = _decision(ctx)
    draft = build_analyst_draft(context=ctx, decision=decision, model=_StructuredModel(_valid_draft(decision)))
    assert isinstance(draft, AnalystDraft)
