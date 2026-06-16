from __future__ import annotations

import inspect
import json

from fastapi.testclient import TestClient

import app.api.v1.routes_layer3 as routes_layer3
from app.main import app
from app.schemas import (
    BlockResponse,
    BlockStatus,
    FetchPlan,
    GateSeverity,
    GateStatus,
    HardGate,
    MissingFields,
    ModeSelection,
    Provenance,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import (
    AnalystDraft,
    AnalystPathNarrative,
    CriticReview,
    CriticVerdict,
    Layer3Result,
    Layer3Status,
)
from app.services.layer2.fact_package_builder import build_rollup, compute_completeness
from app.services.layer3.context_builder import prepare_reasoning_context
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision
from app.services.layer3.graph import run_layer3 as real_run_layer3


# --------------------------------------------------------------------------- #
# factories + stub model
# --------------------------------------------------------------------------- #
def _fp(*, blocks=None, missing=None, modes=None) -> FactPackage:
    modes = modes or [RequestedMode.road]
    single = len(modes) == 1
    request = ValidatedShipmentRequest(
        case_id="case-1",
        mode=ModeSelection(
            requested_mode=modes[0] if single else RequestedMode.unknown,
            candidate_modes=modes,
            needs_mode_selection=not single,
        ),
        missing_fields=missing or MissingFields(high_value=["incoterm"]),
    )
    blocks = blocks if blocks is not None else [
        BlockResponse(block_id="ROAD-A", mode=RequestedMode.road, status=BlockStatus.found, provenance=Provenance(source="test"))
    ]
    rollup = build_rollup(blocks, [], [], [])
    return FactPackage(
        case_id=request.case_id, request=request, fetch_plan=FetchPlan(case_id=request.case_id),
        block_responses=blocks, global_hard_gates=[], global_unknowns=[], global_missing_fields=[],
        conflicts=[], completeness=compute_completeness(rollup, blocks), derived_rollup=rollup,
    )


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


def _matching_draft(decision, *, hide_gates=False) -> AnalystDraft:
    narratives = [
        AnalystPathNarrative(
            path_family=p.path_family, mode=p.mode, rank=p.rank,
            why_ranked_here="Per cited evidence.", why_not_higher="Per cited caps.",
            what_would_improve_readiness=["Resolve cited gaps."],
            evidence_refs=["block:ROAD-A"] if hide_gates else list(p.evidence_refs),
        )
        for p in decision.ranked_path_families
    ]
    return AnalystDraft(case_id=decision.case_id, narratives=narratives, overall_summary="Internal readiness explanation.")


def _rank_one_only_draft(decision) -> AnalystDraft:
    draft = _matching_draft(decision)
    draft.narratives = [n for n in draft.narratives if n.rank == 1]
    return draft


def _invalid_missing_evidence_payload(decision):
    payload = _matching_draft(decision).model_dump(mode="json")
    payload["narratives"][0].pop("evidence_refs")
    return payload


def _pass_result(fp: FactPackage) -> Layer3Result:
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    return real_run_layer3(
        fact_package=fp, trace_id="t1",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )


def _blocked_result(fp: FactPackage) -> Layer3Result:
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    return real_run_layer3(
        fact_package=fp, trace_id="t1",
        analyst_model=_StructuredModel(_matching_draft(decision, hide_gates=True)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )


def _client() -> TestClient:
    return TestClient(app)


def _post(fp: FactPackage):
    with _client() as client:
        return client.post("/api/v1/layer3/reason", json=fp.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# 1 + 3. shape + successful pass
# --------------------------------------------------------------------------- #
def test_endpoint_returns_layer3_result_shape(monkeypatch):
    fp = _fp()
    monkeypatch.setattr(routes_layer3, "run_layer3", lambda **_: _pass_result(fp))
    response = _post(fp)
    assert response.status_code == 200
    body = response.json()
    assert "status" in body and "safety_gate_report" in body and "debug" in body


def test_successful_pass_returns_decision(monkeypatch):
    fp = _fp()
    monkeypatch.setattr(routes_layer3, "run_layer3", lambda **_: _pass_result(fp))
    body = _post(fp).json()
    assert body["status"] == Layer3Status.pass_to_layer4.value
    assert body["reasoning_decision"] is not None
    assert body["reasoning_decision"]["case_id"] == "case-1"


# --------------------------------------------------------------------------- #
# 2 + 14. endpoint calls run_layer3 with a FactPackage + generated trace_id
# --------------------------------------------------------------------------- #
def test_endpoint_calls_run_layer3_with_fact_package(monkeypatch):
    fp = _fp()
    captured: dict = {}

    def _capture(*, fact_package, trace_id=None):
        captured["fact_package"] = fact_package
        captured["trace_id"] = trace_id
        return _pass_result(fp)

    monkeypatch.setattr(routes_layer3, "run_layer3", _capture)
    _post(fp)
    assert isinstance(captured["fact_package"], FactPackage)
    assert isinstance(captured["trace_id"], str) and captured["trace_id"]


# --------------------------------------------------------------------------- #
# 4. blocked result, no traceback
# --------------------------------------------------------------------------- #
def test_blocked_result(monkeypatch):
    fp = _fp(blocks=[BlockResponse(
        block_id="ROAD-A", mode=RequestedMode.road, status=BlockStatus.found,
        hard_gates=[HardGate(gate_id="GB", mode=RequestedMode.road, severity=GateSeverity.blocking, status=GateStatus.triggered, message="m", source_block="ROAD-A")],
        provenance=Provenance(source="test"),
    )])
    monkeypatch.setattr(routes_layer3, "run_layer3", lambda **_: _blocked_result(fp))
    response = _post(fp)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Layer3Status.blocked.value
    assert "Traceback" not in response.text


# --------------------------------------------------------------------------- #
# 5-7. error mapping
# --------------------------------------------------------------------------- #
def test_runtime_error_maps_503(monkeypatch):
    fp = _fp()

    def _boom(**_):
        raise RuntimeError("no model")

    monkeypatch.setattr(routes_layer3, "run_layer3", _boom)
    response = _post(fp)
    assert response.status_code == 503
    assert "Traceback" not in response.text


def test_value_error_maps_422(monkeypatch):
    fp = _fp()

    def _boom(**_):
        raise ValueError("bad draft")

    monkeypatch.setattr(routes_layer3, "run_layer3", _boom)
    response = _post(fp)
    assert response.status_code == 422
    assert "Traceback" not in response.text


def test_unexpected_exception_maps_503(monkeypatch):
    fp = _fp()

    def _boom(**_):
        raise KeyError("surprise")

    monkeypatch.setattr(routes_layer3, "run_layer3", _boom)
    response = _post(fp)
    assert response.status_code == 503
    assert "Traceback" not in response.text


# --------------------------------------------------------------------------- #
# 8 + 9. no raw scores / no InternalScoringTrace in response
# --------------------------------------------------------------------------- #
def test_response_has_no_raw_scores(monkeypatch):
    fp = _fp()
    monkeypatch.setattr(routes_layer3, "run_layer3", lambda **_: _pass_result(fp))
    text = _post(fp).text
    assert "raw_score" not in text
    assert "raw_scores_by_path" not in text
    assert "internal_scoring_trace" not in text
    assert "InternalScoringTrace" not in text


# --------------------------------------------------------------------------- #
# 10. endpoint does not call Layer 1 or Layer 2
# --------------------------------------------------------------------------- #
def test_endpoint_does_not_import_layer1_or_layer2():
    source = inspect.getsource(routes_layer3)
    assert "services.layer1" not in source
    assert "services.layer2" not in source


def test_endpoint_does_not_invoke_layer1_or_layer2(monkeypatch):
    import app.services.layer1 as layer1
    import app.services.layer2.service as layer2_service

    def _boom(*args, **kwargs):
        raise AssertionError("Layer 1/2 must not be called by the Layer 3 endpoint")

    monkeypatch.setattr(layer1, "handle_intake_message", _boom, raising=False)
    monkeypatch.setattr(layer2_service, "build_fact_package_for_request", _boom, raising=False)
    fp = _fp()
    monkeypatch.setattr(routes_layer3, "run_layer3", lambda **_: _pass_result(fp))
    assert _post(fp).status_code == 200


# --------------------------------------------------------------------------- #
# 11-13. routing registration / other endpoints unchanged
# --------------------------------------------------------------------------- #
def test_layer3_route_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/layer3/reason" in paths


def test_cortex_and_intake_routes_unchanged():
    paths = {route.path for route in app.routes}
    assert "/api/v1/cortex/message" in paths
    assert "/api/v1/intake/message" in paths


# --------------------------------------------------------------------------- #
# integration-style: real run_layer3 with stub models through the endpoint
# --------------------------------------------------------------------------- #
def test_integration_real_graph_with_stub_models(monkeypatch):
    fp = _fp()
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    analyst_stub = _StructuredModel(_matching_draft(decision))

    def _patched(*, fact_package, trace_id=None):
        return real_run_layer3(fact_package=fact_package, trace_id=trace_id, analyst_model=analyst_stub)

    monkeypatch.setattr(routes_layer3, "run_layer3", _patched)
    body = _post(fp).json()
    assert body["status"] == Layer3Status.pass_to_layer4.value
    assert body["reasoning_decision"] is not None


def test_endpoint_real_graph_analyst_structured_none_json_fallback_passes(monkeypatch):
    fp = _fp()
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    analyst_stub = _StructuredThenJsonModel(
        structured_result=None,
        fallback_payload=_matching_draft(decision).model_dump(mode="json"),
    )

    def _patched(*, fact_package, trace_id=None):
        return real_run_layer3(fact_package=fact_package, trace_id=trace_id, analyst_model=analyst_stub)

    monkeypatch.setattr(routes_layer3, "run_layer3", _patched)
    response = _post(fp)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Layer3Status.pass_to_layer4.value
    assert body["reasoning_decision"] is not None
    assert body["debug"]["revision_count"] == 0
    assert "analyst_error" not in body["debug"]
    assert "ANALYST_CONTRACT_FAILED" not in response.text
    assert "raw_score" not in response.text
    assert "internal_scoring_trace" not in response.text


def test_endpoint_real_graph_analyst_thought_part_json_fallback_passes(monkeypatch):
    fp = _fp()
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    analyst_stub = _StructuredThenContentModel(
        structured_result=None,
        fallback_content=[
            {"text": '{"case_id":"thought","raw_score":99}', "thought": True},
            {"text": json.dumps(_matching_draft(decision).model_dump(mode="json"))},
        ],
    )

    def _patched(*, fact_package, trace_id=None):
        return real_run_layer3(fact_package=fact_package, trace_id=trace_id, analyst_model=analyst_stub)

    monkeypatch.setattr(routes_layer3, "run_layer3", _patched)
    response = _post(fp)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Layer3Status.pass_to_layer4.value
    assert body["reasoning_decision"] is not None
    assert "analyst_error" not in body["debug"]
    assert "ANALYST_CONTRACT_FAILED" not in response.text
    assert "raw_score" not in response.text
    assert "internal_scoring_trace" not in response.text


def test_endpoint_real_graph_analyst_evidence_failure_returns_blocked_not_422(monkeypatch):
    fp = _fp()
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    analyst_stub = _SequenceStructuredModel(
        [
            _invalid_missing_evidence_payload(decision),
            _invalid_missing_evidence_payload(decision),
        ]
    )

    def _patched(*, fact_package, trace_id=None):
        return real_run_layer3(
            fact_package=fact_package,
            trace_id=trace_id,
            analyst_model=analyst_stub,
            max_revisions=1,
        )

    monkeypatch.setattr(routes_layer3, "run_layer3", _patched)
    response = _post(fp)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Layer3Status.blocked.value
    assert body["safety_gate_report"]["passed"] is False
    assert body["safety_gate_report"]["violations"][0]["code"] == "ANALYST_CONTRACT_FAILED"
    assert "analyst_error" in body["debug"]
    assert "raw_score" not in response.text
    assert "internal_scoring_trace" not in response.text


def test_endpoint_real_graph_partial_analyst_narratives_returns_layer3_result(monkeypatch):
    fp = _fp(modes=[RequestedMode.sea, RequestedMode.air, RequestedMode.road])
    ctx = prepare_reasoning_context(fp)
    decision, _ = build_deterministic_decision(ctx, trace_id="t1")
    analyst_stub = _SequenceStructuredModel(
        [
            _rank_one_only_draft(decision),
            _rank_one_only_draft(decision),
        ]
    )

    def _patched(*, fact_package, trace_id=None):
        return real_run_layer3(
            fact_package=fact_package,
            trace_id=trace_id,
            analyst_model=analyst_stub,
            max_revisions=1,
        )

    monkeypatch.setattr(routes_layer3, "run_layer3", _patched)
    response = _post(fp)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == Layer3Status.blocked.value
    assert body["safety_gate_report"]["violations"][0]["code"] == "ANALYST_CONTRACT_FAILED"
    assert "Analyst omitted required narratives" in body["debug"]["analyst_error"]
    assert "raw_score" not in response.text
    assert "internal_scoring_trace" not in response.text


def test_invalid_fact_package_still_returns_422():
    with _client() as client:
        response = client.post("/api/v1/layer3/reason", json={"case_id": "bad"})

    assert response.status_code == 422
