from __future__ import annotations

import json

from fastapi.testclient import TestClient

import app.api.v1.routes_agent_runs as routes_agent_runs
from app.config import settings
from app.main import app
from app.schemas.layer3 import CriticReview, CriticVerdict
from app.services.layer3 import run_layer3
from tests.layer3.test_graph import _StructuredModel, _decision_for, _fp, _matching_draft, _request


def test_layer3_analyst_run_is_recorded(agent_run_repo):
    fp = _fp(request=_request())
    _, decision = _decision_for(fp)

    run_layer3(
        fact_package=fp,
        trace_id="trace-analyst",
        conversation_id="conv-analyst",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )

    analyst = _only_run(agent_run_repo, "layer3_analyst")
    assert analyst.case_id == fp.case_id
    assert analyst.conversation_id == "conv-analyst"
    assert analyst.trace_id == "trace-analyst"
    assert analyst.prompt_chars > 0
    assert analyst.prompt_rough_tokens > 0
    assert analyst.response_chars > 0
    assert analyst.response_rough_tokens > 0
    assert analyst.output_json["overall_summary"] == "Internal readiness explanation."


def test_layer3_critic_run_is_recorded(agent_run_repo):
    fp = _fp(request=_request(active_profiles=["dangerous_goods"]))
    _, decision = _decision_for(fp)

    run_layer3(
        fact_package=fp,
        trace_id="trace-critic",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )

    critic = _only_run(agent_run_repo, "layer3_critic")
    assert critic.status.value == "success"
    assert critic.output_json["verdict"] == "pass"
    assert critic.prompt_chars > 0


def test_layer3_safety_gate_run_is_recorded(agent_run_repo):
    fp = _fp(request=_request())
    _, decision = _decision_for(fp)

    run_layer3(
        fact_package=fp,
        trace_id="trace-safety",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )

    safety = _only_run(agent_run_repo, "layer3_safety_gate")
    assert safety.provider == "deterministic"
    assert safety.safety_report is not None
    assert safety.safety_report["passed"] is True
    assert safety.output_json["status"] == "pass"


def test_agent_run_does_not_store_full_prompt_by_default(agent_run_repo, monkeypatch):
    monkeypatch.setattr(settings, "cortex_trace_full_prompts", False)
    fp = _fp(request=_request())
    _, decision = _decision_for(fp)

    run_layer3(
        fact_package=fp,
        trace_id="trace-no-full-prompt",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )

    analyst = _only_run(agent_run_repo, "layer3_analyst")
    assert analyst.prompt_artifact_ref is None
    assert analyst.response_artifact_ref is None
    assert "You are the Cortex Layer 3 Analyst Agent" not in json.dumps(
        analyst.model_dump(mode="json")
    )


def test_agent_run_stores_prompt_artifact_ref_when_full_prompt_trace_enabled(
    agent_run_repo,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(settings, "cortex_trace_full_prompts", True)
    monkeypatch.setattr(settings, "cortex_trace_artifact_dir", str(tmp_path))
    fp = _fp(request=_request())
    _, decision = _decision_for(fp)

    run_layer3(
        fact_package=fp,
        trace_id="trace-full-prompt",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )

    analyst = _only_run(agent_run_repo, "layer3_analyst")
    assert analyst.prompt_artifact_ref is not None
    assert analyst.response_artifact_ref is not None
    assert list(tmp_path.glob(f"{analyst.id}.prompt.txt"))
    assert list(tmp_path.glob(f"{analyst.id}.response.txt"))


def test_debug_agent_runs_endpoint_returns_analyst_and_critic_outputs(
    agent_run_repo,
    monkeypatch,
):
    monkeypatch.setattr(routes_agent_runs, "_REPOSITORY", agent_run_repo)
    fp = _fp(request=_request(active_profiles=["dangerous_goods"]))
    _, decision = _decision_for(fp)

    run_layer3(
        fact_package=fp,
        trace_id="trace-debug-endpoint",
        analyst_model=_StructuredModel(_matching_draft(decision)),
        critic_model=_StructuredModel(CriticReview(verdict=CriticVerdict.pass_)),
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/cortex/cases/{fp.case_id}/agent-runs")

    assert response.status_code == 200
    body = response.json()
    runs = {run["agent_name"]: run for run in body["runs"]}
    assert runs["layer3_analyst"]["output_json"]["overall_summary"] == "Internal readiness explanation."
    assert runs["layer3_critic"]["output_json"]["verdict"] == "pass"


def _only_run(repo, agent_name: str):
    matches = [record for record in repo.records if record.agent_name == agent_name]
    assert len(matches) == 1
    return matches[0]
