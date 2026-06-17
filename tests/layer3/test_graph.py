from __future__ import annotations

import json

from app.schemas import (
    BlockResponse,
    BlockStatus,
    Conflict,
    FetchPlan,
    GateSeverity,
    GateStatus,
    HardGate,
    MissingFields,
    ModeSelection,
    Provenance,
    RequestedMode,
    Unknown,
    ValidatedShipmentRequest,
)
from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    CriticFinding,
    CriticReview,
    CriticVerdict,
    Layer3Result,
    Layer3Status,
)
from app.services.layer2.fact_package_builder import build_rollup, compute_completeness
from app.services.layer3 import run_layer3
from app.services.layer3.agents import analyst_agent, critic_agent
from app.services.layer3.context_builder import prepare_reasoning_context
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision


# --------------------------------------------------------------------------- #
# FactPackage factory
# --------------------------------------------------------------------------- #
def _request(*, modes=None, active_profiles=None, missing=None) -> ValidatedShipmentRequest:
    modes = modes or [RequestedMode.road]
    single = len(modes) == 1
    return ValidatedShipmentRequest(
        case_id="case-1",
        mode=ModeSelection(
            requested_mode=modes[0] if single else RequestedMode.unknown,
            candidate_modes=modes,
            needs_mode_selection=not single,
        ),
        active_profiles=active_profiles or [],
        missing_fields=missing or MissingFields(),
    )


def _block(block_id, mode, *, status=BlockStatus.found, hard_gates=None, unknowns=None) -> BlockResponse:
    return BlockResponse(
        block_id=block_id, mode=mode, status=status,
        hard_gates=hard_gates or [], unknowns=unknowns or [],
        provenance=Provenance(source="test"),
    )


def _fp(*, request=None, blocks=None, conflicts=None, completeness=None) -> FactPackage:
    request = request or _request()
    blocks = blocks if blocks is not None else [_block("ROAD-A", RequestedMode.road)]
    rollup = build_rollup(blocks, [], [], [])
    completeness = completeness or compute_completeness(rollup, blocks)
    return FactPackage(
        case_id=request.case_id, request=request, fetch_plan=FetchPlan(case_id=request.case_id),
        block_responses=blocks, global_hard_gates=[], global_unknowns=[], global_missing_fields=[],
        conflicts=conflicts or [], completeness=completeness, derived_rollup=rollup,
    )


def _gate(code, mode, severity, status) -> HardGate:
    return HardGate(
        gate_id=code, mode=mode, severity=severity, status=status,
        message=code, source_block=f"{mode.value.upper()}-A",
    )


# --------------------------------------------------------------------------- #
# decision-aware draft + stub models
# --------------------------------------------------------------------------- #
def _decision_for(fp: FactPackage):
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    return ctx, decision


def _matching_draft(decision, *, disputes=False) -> AnalystDraft:
    narratives = [
        AnalystPathNarrative(
            path_family=p.path_family, mode=p.mode, rank=p.rank,
            why_ranked_here="Per cited evidence.", why_not_higher="Per cited caps.",
            what_would_improve_readiness=["Resolve cited gaps."],
            evidence_refs=list(p.evidence_refs),
        )
        for p in decision.ranked_path_families
    ]
    return AnalystDraft(
        case_id=decision.case_id, narratives=narratives,
        overall_summary="Internal readiness explanation.",
        disputes_ranking=disputes,
        dispute_reason="Evidence ordering is unclear." if disputes else None,
    )


def _rank_one_only_draft(decision) -> AnalystDraft:
    draft = _matching_draft(decision)
    draft.narratives = [n for n in draft.narratives if n.rank == 1]
    return draft


class _Runnable:
    def __init__(self, result):
        self.result = result

    def invoke(self, prompt):
        return self.result


class _StructuredModel:
    def __init__(self, result):
        self.result = result

    def with_structured_output(self, schema):
        return _Runnable(self.result)


class _StructuredThenJsonRunnable:
    def __init__(self, parent: "_StructuredThenJsonModel"):
        self.parent = parent

    def invoke(self, prompt):
        self.parent.structured_prompt = prompt
        return self.parent.structured_result


class _StructuredThenJsonModel:
    def __init__(self, *, structured_result, fallback_payload):
        self.structured_result = structured_result
        self.fallback_payload = fallback_payload
        self.structured_called = False
        self.fallback_called = False
        self.structured_prompt: str | None = None
        self.fallback_prompt: str | None = None

    def with_structured_output(self, schema):
        self.structured_called = True
        return _StructuredThenJsonRunnable(self)

    def invoke(self, prompt):
        self.fallback_called = True
        self.fallback_prompt = prompt
        return (
            json.dumps(self.fallback_payload)
            if isinstance(self.fallback_payload, (dict, list))
            else self.fallback_payload
        )


class _StructuredThenContentModel:
    def __init__(self, *, structured_result, fallback_content):
        self.structured_result = structured_result
        self.fallback_content = fallback_content
        self.structured_called = False
        self.fallback_called = False
        self.structured_prompt: str | None = None
        self.fallback_prompt: str | None = None

    def with_structured_output(self, schema):
        self.structured_called = True
        return _StructuredThenJsonRunnable(self)

    def invoke(self, prompt):
        self.fallback_called = True
        self.fallback_prompt = prompt
        return self.fallback_content


class _SequenceRunnable:
    def __init__(self, parent: "_SequenceStructuredModel"):
        self.parent = parent

    def invoke(self, prompt):
        self.parent.prompts.append(prompt)
        if len(self.parent.results) == 1:
            return self.parent.results[0]
        return self.parent.results.pop(0)


class _SequenceStructuredModel:
    def __init__(self, results):
        self.results = list(results)
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        return _SequenceRunnable(self)


def _invalid_missing_evidence_payload(decision):
    payload = _matching_draft(decision).model_dump(mode="json")
    payload["narratives"][0].pop("evidence_refs")
    return payload


# --------------------------------------------------------------------------- #
# 1 + 4 + 15. simple cargo: critic skipped, gate still runs, pass terminal
# --------------------------------------------------------------------------- #
def test_simple_cargo_skips_critic_and_passes():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    result = run_layer3(fact_package=fp, analyst_model=_StructuredModel(_matching_draft(decision)))
    assert isinstance(result, Layer3Result)
    assert result.critic_review.verdict is CriticVerdict.skipped   # 1 + 15
    assert result.safety_gate_report is not None                   # 4
    assert result.safety_gate_report.passed is True
    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None


def test_analyst_structured_none_json_fallback_passes_without_revision():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    analyst_model = _StructuredThenJsonModel(
        structured_result=None,
        fallback_payload=_matching_draft(decision).model_dump(mode="json"),
    )

    result = run_layer3(fact_package=fp, analyst_model=analyst_model)

    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None
    assert result.debug["revision_count"] == 0
    assert "analyst_error" not in result.debug
    assert analyst_model.structured_called is True
    assert analyst_model.fallback_called is True


def test_analyst_json_fallback_with_thought_part_reaches_pass_to_layer4():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    analyst_model = _StructuredThenContentModel(
        structured_result=None,
        fallback_content=[
            {"text": '{"case_id":"thought","raw_score":99}', "thought": True},
            {"text": json.dumps(_matching_draft(decision).model_dump(mode="json"))},
        ],
    )

    result = run_layer3(fact_package=fp, analyst_model=analyst_model)

    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None
    assert "analyst_error" not in result.debug
    dumped = str(result.model_dump())
    assert "ANALYST_CONTRACT_FAILED" not in dumped
    assert "raw_score" not in dumped
    assert "internal_scoring_trace" not in dumped


# --------------------------------------------------------------------------- #
# 2 + 17. critic runs for DG / lithium profiles
# --------------------------------------------------------------------------- #
def test_critic_runs_for_dangerous_goods():
    fp = _fp(request=_request(active_profiles=["dangerous_goods"]))
    _, decision = _decision_for(fp)
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )
    assert result.critic_review.verdict is CriticVerdict.pass_  # ran, not skipped


def test_critic_runs_for_lithium_battery():
    fp = _fp(request=_request(active_profiles=["lithium_battery"]))
    _, decision = _decision_for(fp)
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )
    assert result.critic_review.verdict is CriticVerdict.pass_


# --------------------------------------------------------------------------- #
# 3. critic runs when analyst disputes
# --------------------------------------------------------------------------- #
def test_critic_runs_when_analyst_disputes():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(_matching_draft(decision, disputes=True)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
        max_revisions=1,
    )
    assert result.critic_review.verdict is not CriticVerdict.skipped


def test_analyst_validation_failure_revises_then_passes():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    analyst_model = _SequenceStructuredModel(
        [
            _invalid_missing_evidence_payload(decision),
            _matching_draft(decision),
        ]
    )

    result = run_layer3(fact_package=fp, analyst_model=analyst_model, max_revisions=1)

    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None
    assert result.debug["revision_count"] == 1
    assert "analyst_error" not in result.debug
    assert len(analyst_model.prompts) == 2
    assert "Previous output failed validation" in analyst_model.prompts[1]
    assert "Analyst structured output did not match AnalystDraft" in analyst_model.prompts[1]


def test_analyst_validation_failure_blocks_after_revision_budget():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    analyst_model = _SequenceStructuredModel(
        [
            _invalid_missing_evidence_payload(decision),
            _invalid_missing_evidence_payload(decision),
        ]
    )

    result = run_layer3(fact_package=fp, analyst_model=analyst_model, max_revisions=1)

    assert result.status is Layer3Status.blocked
    assert result.reasoning_decision is None
    assert result.debug["revision_count"] == 1
    assert "analyst_error" in result.debug
    assert result.safety_gate_report is not None
    assert result.safety_gate_report.passed is False
    assert {
        violation.code for violation in result.safety_gate_report.violations
    } == {"ANALYST_CONTRACT_FAILED"}
    dumped = str(result.model_dump())
    assert "raw_score" not in dumped
    assert "internal_scoring_trace" not in dumped


def test_partial_analyst_draft_revises_with_missing_path_feedback_then_passes():
    fp = _fp(
        request=_request(
            modes=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
            missing=MissingFields(high_value=["incoterm"]),
        )
    )
    _, decision = _decision_for(fp)
    analyst_model = _SequenceStructuredModel(
        [
            _rank_one_only_draft(decision),
            _matching_draft(decision),
        ]
    )

    result = run_layer3(fact_package=fp, analyst_model=analyst_model, max_revisions=1)

    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None
    assert result.debug["revision_count"] == 1
    assert "analyst_error" not in result.debug
    assert len(analyst_model.prompts) == 2
    revision_prompt = analyst_model.prompts[1]
    assert "Previous output omitted required narratives:" in revision_prompt
    assert "Return all required narratives in the next JSON output" in revision_prompt
    for path in decision.ranked_path_families:
        if path.rank == 1:
            continue
        assert f"rank {path.rank} / {path.mode.value} / {path.path_family}" in revision_prompt


def test_partial_analyst_draft_blocks_after_revision_budget():
    fp = _fp(
        request=_request(
            modes=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
            missing=MissingFields(high_value=["incoterm"]),
        )
    )
    _, decision = _decision_for(fp)
    analyst_model = _SequenceStructuredModel(
        [
            _rank_one_only_draft(decision),
            _rank_one_only_draft(decision),
        ]
    )

    result = run_layer3(fact_package=fp, analyst_model=analyst_model, max_revisions=1)

    assert result.status is Layer3Status.blocked
    assert result.reasoning_decision is None
    assert result.debug["revision_count"] == 1
    assert "analyst_error" in result.debug
    assert result.safety_gate_report is not None
    assert {
        violation.code for violation in result.safety_gate_report.violations
    } == {"ANALYST_CONTRACT_FAILED"}
    dumped = str(result.model_dump())
    assert "raw_score" not in dumped
    assert "internal_scoring_trace" not in dumped


# --------------------------------------------------------------------------- #
# 5. safety gate block -> blocked (hidden triggered blocking gate)
# --------------------------------------------------------------------------- #
def test_safety_gate_block_produces_blocked():
    fp = _fp(blocks=[_block("ROAD-A", RequestedMode.road, hard_gates=[_gate("GB", RequestedMode.road, GateSeverity.blocking, GateStatus.triggered)])])
    _, decision = _decision_for(fp)
    draft = _matching_draft(decision)
    draft.narratives[0].evidence_refs = ["block:ROAD-A"]  # hide the gate ref
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(draft),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )
    assert result.status is Layer3Status.blocked
    assert result.safety_gate_report.passed is False


# --------------------------------------------------------------------------- #
# 6. critic block + gate failure -> blocked (hidden high unknown -> gate revise)
# --------------------------------------------------------------------------- #
def test_critic_block_with_gate_revise_blocks():
    # DG profile makes the unknown critical; the draft hides it -> gate revise
    # (not passed). Critic block + gate not passed -> blocked (routing rule 3).
    fp = _fp(
        request=_request(active_profiles=["dangerous_goods"]),
        blocks=[_block("ROAD-A", RequestedMode.road, unknowns=[Unknown(field="classification", reason="r")])],
    )
    _, decision = _decision_for(fp)
    draft = _matching_draft(decision)
    for n in draft.narratives:
        n.evidence_refs = ["block:ROAD-A"]  # hide the DG-critical unknown
    critic_block = CriticReview(
        verdict=CriticVerdict.block,
        findings=[CriticFinding(code="X", severity="blocking", message="Unsupported claim.")],
    )
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(draft),
        critic_model=_StructuredModel(critic_block),
        max_revisions=1,
    )
    assert result.status is Layer3Status.blocked


# --------------------------------------------------------------------------- #
# 7. critic revise triggers one revision (max_revisions=1) then passes
# --------------------------------------------------------------------------- #
def test_critic_revise_triggers_one_revision():
    fp = _fp(request=_request(active_profiles=["dangerous_goods"]))  # forces critic to run
    _, decision = _decision_for(fp)
    critic_revise = CriticReview(
        verdict=CriticVerdict.revise,
        findings=[CriticFinding(code="VAGUE", severity="medium", message="Vague.")],
        required_changes=["Clarify."],
    )
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(critic_revise),
        max_revisions=1,
    )
    assert result.debug["revision_count"] == 1
    # gate passes; after budget is spent the advisory revise no longer loops
    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None
    # the advisory critic revise is recorded but did not block the decision build
    assert result.critic_review.verdict is CriticVerdict.revise


# --------------------------------------------------------------------------- #
# 8 + 9. revision loop stops after max_revisions; no infinite loop
# --------------------------------------------------------------------------- #
def test_revision_loop_stops_and_blocks():
    # DG + hidden unknown -> gate revise every time -> block after budget
    fp = _fp(
        request=_request(active_profiles=["dangerous_goods"]),
        blocks=[_block("ROAD-A", RequestedMode.road, unknowns=[Unknown(field="classification", reason="r")])],
    )
    _, decision = _decision_for(fp)
    draft = _matching_draft(decision)
    for n in draft.narratives:
        n.evidence_refs = ["block:ROAD-A"]  # hide the (DG-critical) unknown -> gate revise
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(draft),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
        max_revisions=1,
    )
    assert result.status is Layer3Status.blocked
    assert result.debug["revision_count"] == 1


# --------------------------------------------------------------------------- #
# 10. no mutation of FactPackage
# --------------------------------------------------------------------------- #
def test_does_not_mutate_fact_package():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    before = fp.model_dump()
    run_layer3(fact_package=fp, analyst_model=_StructuredModel(_matching_draft(decision)))
    assert fp.model_dump() == before


# --------------------------------------------------------------------------- #
# 11. internal_trace_ref only in debug; no raw scores in public result
# --------------------------------------------------------------------------- #
def test_internal_trace_ref_in_debug_no_raw_scores():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    result = run_layer3(fact_package=fp, trace_id="abc", analyst_model=_StructuredModel(_matching_draft(decision)))
    assert result.debug["internal_trace_ref"] == "abc"
    dumped = str(result.model_dump())
    assert "raw_score" not in dumped
    assert "raw_scores_by_path" not in dumped


# --------------------------------------------------------------------------- #
# 12. trace_id carried through engine
# --------------------------------------------------------------------------- #
def test_trace_id_carried_through():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    result = run_layer3(fact_package=fp, trace_id="trace-xyz", analyst_model=_StructuredModel(_matching_draft(decision)))
    assert result.debug["internal_trace_ref"] == "trace-xyz"


# --------------------------------------------------------------------------- #
# 13. no live LLM call
# --------------------------------------------------------------------------- #
def test_no_live_llm_call(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("get_chat_model must not be called when models are injected")

    monkeypatch.setattr(analyst_agent, "get_chat_model", _boom)
    monkeypatch.setattr(critic_agent, "get_chat_model", _boom)
    fp = _fp(request=_request(active_profiles=["dangerous_goods"]))
    _, decision = _decision_for(fp)
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )
    assert isinstance(result, Layer3Result)


# --------------------------------------------------------------------------- #
# 16. conflict case runs critic
# --------------------------------------------------------------------------- #
def test_conflict_case_runs_critic():
    fp = _fp(conflicts=[Conflict(type="CARGO_UN_MISMATCH", message="m")])
    _, decision = _decision_for(fp)
    result = run_layer3(
        fact_package=fp,
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )
    assert result.critic_review.verdict is not CriticVerdict.skipped


# --------------------------------------------------------------------------- #
# 18. every terminal returns a schema-valid Layer3Result
# --------------------------------------------------------------------------- #
def test_clarification_terminal_is_schema_valid():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    draft = _matching_draft(decision)
    draft.user_clarification_questions = ["What is the incoterm?"]
    result = run_layer3(fact_package=fp, analyst_model=_StructuredModel(draft))
    assert isinstance(result, Layer3Result)
    assert result.status is Layer3Status.request_user_clarification


def test_terminals_are_layer3_results():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    passed = run_layer3(fact_package=fp, analyst_model=_StructuredModel(_matching_draft(decision)))
    assert isinstance(passed, Layer3Result)


# --------------------------------------------------------------------------- #
# 19. run_layer3 exported from app.services.layer3
# --------------------------------------------------------------------------- #
def test_run_layer3_is_exported():
    assert callable(run_layer3)


# --------------------------------------------------------------------------- #
# freeze audit: end-to-end determinism of the full pipeline
# --------------------------------------------------------------------------- #
def test_end_to_end_determinism():
    # same FactPackage + same stub models + same trace_id -> identical
    # ReasoningDecision.model_dump() and identical Layer3Result status/debug.
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    draft = _matching_draft(decision)
    first = run_layer3(fact_package=fp, trace_id="t-fixed", analyst_model=_StructuredModel(draft))
    second = run_layer3(fact_package=fp, trace_id="t-fixed", analyst_model=_StructuredModel(draft))
    assert first.reasoning_decision is not None
    assert first.reasoning_decision.model_dump() == second.reasoning_decision.model_dump()
    assert first.status is second.status
    assert first.debug == second.debug


# --------------------------------------------------------------------------- #
# pass terminal now carries a real ReasoningDecision (Step 9)
# --------------------------------------------------------------------------- #
def test_pass_terminal_carries_reasoning_decision():
    fp = _fp(request=_request(missing=MissingFields(high_value=["incoterm"])))
    _, decision = _decision_for(fp)
    result = run_layer3(fact_package=fp, analyst_model=_StructuredModel(_matching_draft(decision)))
    assert result.status is Layer3Status.pass_to_layer4
    assert result.reasoning_decision is not None
    assert result.reasoning_decision.case_id == fp.case_id
    # no raw scores leaked into the seam
    dumped = str(result.reasoning_decision.model_dump())
    assert "raw_score" not in dumped
    assert "raw_scores_by_path" not in dumped
