from app.schemas import (
    BlockStatus,
    EmptyResponseBehavior,
    FallbackPolicy,
    FetchPlan,
    FetchPlanItem,
    FetchPriority,
    Lane,
    ModeSelection,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_executor import execute_fetch_plan
from app.services.layer2.fetch_planner import build_fetch_plan


def _request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-layer2-executor-001",
        lane=Lane(origin_country="CN", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
    )


def test_executor_calls_road_c_for_road_plan():
    request = _request()
    plan = build_fetch_plan(request)

    responses = execute_fetch_plan(request, plan)

    assert len(responses) == 5
    assert responses[0].block_id == "ROAD-C"
    assert responses[0].status == BlockStatus.found
    assert len(responses[0].hard_gates) == 1
    assert (
        responses[0].hard_gates[0].gate_id
        == "ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL"
    )
    assert [response.status for response in responses[1:]] == [
        BlockStatus.skipped,
        BlockStatus.skipped,
        BlockStatus.skipped,
        BlockStatus.skipped,
    ]


def test_executor_unknown_block_returns_error_response():
    request = _request()
    plan = FetchPlan(
        case_id=request.case_id,
        items=[
            FetchPlanItem(
                block_id="UNKNOWN-BLOCK",
                mode=RequestedMode.road,
                reason="test unknown connector",
                priority=FetchPriority.required,
                required_inputs=[],
                empty_behavior=EmptyResponseBehavior.hard_unknown,
                fallback_policy=FallbackPolicy.return_unknown,
            )
        ],
    )

    responses = execute_fetch_plan(request, plan)

    assert len(responses) == 1
    response = responses[0]
    assert response.status == BlockStatus.error
    assert response.block_id == "UNKNOWN-BLOCK"
    assert response.unknowns
    assert response.unknowns[0].field == "fetch_executor.block_id"
    assert response.confidence.source_confidence == SourceConfidence.unknown
