from __future__ import annotations

from app.config import settings
from app.schemas.intake import CaseAction, IntakeDecision, IntakeIntent, IntakeResult
from app.schemas.layer3 import Layer3Result, Layer3Status
from app.schemas.layer4 import Layer4Result
from app.schemas.shipment_request import MissingFields, ValidatedShipmentRequest
from app.services.orchestrator.full_graph import CortexFullGraph


def test_public_full_message_response_still_hides_layer3_by_default(monkeypatch):
    monkeypatch.setattr(settings, "full_response_include_artifacts", False)
    layer1 = IntakeResult(
        conversation_id="conv-public",
        case_id="case-public",
        case_action=CaseAction.create_new_case,
        intent=IntakeIntent.shipment_readiness,
        decision=IntakeDecision.ready_for_layer_2,
        assistant_message="ok",
        ready_for_layer_2=True,
        intake_json=ValidatedShipmentRequest(
            case_id="case-public",
            missing_fields=MissingFields(),
        ),
    )
    layer3 = Layer3Result(case_id="case-public", status=Layer3Status.blocked)
    layer4 = Layer4Result(case_id="case-public", assistant_message="final report")

    update = CortexFullGraph._final_report_node(
        {
            "layer1": layer1,
            "layer3": layer3,
            "layer4": layer4,
            "trace_id": "trace-public",
            "cache_status": {"layer3": {"key": "layer3-artifact-key"}},
        }
    )

    result = update["result"]
    assert result.layer3 is None
    assert result.artifact_refs["layer3"] == "layer3-artifact-key"
    assert result.assistant_message == "final report"
