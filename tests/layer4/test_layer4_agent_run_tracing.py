from __future__ import annotations

from app.services.layer4 import build_layer4_report
from tests.prompt_budget_helpers import build_prompt_budget_case


class _ReportModel:
    model = "trace-report-model"

    def invoke(self, prompt):
        return "Layer 4 readiness report."


def test_layer4_report_run_is_recorded(agent_run_repo):
    case = build_prompt_budget_case()

    build_layer4_report(
        case["layer4_request"],
        model=_ReportModel(),
        trace_id="trace-layer4",
        conversation_id="conv-layer4",
        run_order=5,
    )

    matches = [record for record in agent_run_repo.records if record.agent_name == "layer4_report"]
    assert len(matches) == 1
    record = matches[0]
    assert record.layer == 4
    assert record.run_order == 5
    assert record.conversation_id == "conv-layer4"
    assert record.prompt_chars > 0
    assert record.response_chars == len("Layer 4 readiness report.")
    assert record.output_json["assistant_message"] == "Layer 4 readiness report."
