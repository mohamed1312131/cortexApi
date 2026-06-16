from app.schemas import (
    BlockResponse,
    BlockStatus,
    EmptyResponseBehavior,
    FallbackPolicy,
    FetchPlan,
    FetchPlanItem,
    FetchPriority,
    GateSeverity,
    GateStatus,
    HardGate,
    Lane,
    ModeSelection,
    ProviderUsed,
    Provenance,
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


def test_executor_skips_same_mode_after_required_blocking_gate(monkeypatch):
    request = _request()
    calls: list[str] = []

    def fake_connector(block_id: str):
        def _connector(_request: ValidatedShipmentRequest) -> BlockResponse:
            calls.append(block_id)
            if block_id == "SEA-GATE":
                return _response(
                    block_id,
                    RequestedMode.sea,
                    hard_gates=[_blocking_gate(block_id, RequestedMode.sea)],
                )
            return _response(block_id, RequestedMode.sea)

        return _connector

    monkeypatch.setattr(
        "app.services.layer2.fetch_executor.get_connector",
        fake_connector,
    )
    plan = FetchPlan(
        case_id=request.case_id,
        items=[
            _plan_item("SEA-GATE", RequestedMode.sea, FetchPriority.required),
            _plan_item("SEA-LATER", RequestedMode.sea, FetchPriority.optional),
        ],
    )

    responses = execute_fetch_plan(request, plan)

    assert [response.block_id for response in responses] == ["SEA-GATE", "SEA-LATER"]
    assert responses[0].status == BlockStatus.found
    assert responses[1].status == BlockStatus.skipped
    assert calls == ["SEA-GATE"]
    assert "SEA-GATE" in responses[1].unknowns[0].reason
    assert "SEA-GATE_BLOCKING" in responses[1].unknowns[0].reason
    assert responses[1].provenance.extra == {
        "skipped_after": "SEA-GATE",
        "blocking_gate_id": "SEA-GATE_BLOCKING",
    }


def test_executor_blocking_gate_skip_is_mode_isolated(monkeypatch):
    request = _request()
    calls: list[str] = []

    def fake_connector(block_id: str):
        def _connector(_request: ValidatedShipmentRequest) -> BlockResponse:
            calls.append(block_id)
            mode = {
                "SEA-GATE": RequestedMode.sea,
                "AIR-CHECK": RequestedMode.air,
                "SEA-LATER": RequestedMode.sea,
                "ROAD-CHECK": RequestedMode.road,
            }[block_id]
            if block_id == "SEA-GATE":
                return _response(
                    block_id,
                    mode,
                    hard_gates=[_blocking_gate(block_id, mode)],
                )
            return _response(block_id, mode)

        return _connector

    monkeypatch.setattr(
        "app.services.layer2.fetch_executor.get_connector",
        fake_connector,
    )
    plan = FetchPlan(
        case_id=request.case_id,
        items=[
            _plan_item("SEA-GATE", RequestedMode.sea, FetchPriority.required),
            _plan_item("AIR-CHECK", RequestedMode.air, FetchPriority.required),
            _plan_item("SEA-LATER", RequestedMode.sea, FetchPriority.optional),
            _plan_item("ROAD-CHECK", RequestedMode.road, FetchPriority.required),
        ],
    )

    responses = execute_fetch_plan(request, plan)

    assert [(response.block_id, response.status) for response in responses] == [
        ("SEA-GATE", BlockStatus.found),
        ("AIR-CHECK", BlockStatus.found),
        ("SEA-LATER", BlockStatus.skipped),
        ("ROAD-CHECK", BlockStatus.found),
    ]
    assert calls == ["SEA-GATE", "AIR-CHECK", "ROAD-CHECK"]


def _plan_item(
    block_id: str,
    mode: RequestedMode,
    priority: FetchPriority,
) -> FetchPlanItem:
    return FetchPlanItem(
        block_id=block_id,
        mode=mode,
        reason=f"test plan item {block_id}",
        priority=priority,
        required_inputs=[],
        empty_behavior=EmptyResponseBehavior.hard_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _response(
    block_id: str,
    mode: RequestedMode,
    hard_gates: list[HardGate] | None = None,
) -> BlockResponse:
    return BlockResponse(
        block_id=block_id,
        mode=mode,
        status=BlockStatus.found,
        hard_gates=hard_gates or [],
        provenance=Provenance(source="test", provider_used=ProviderUsed.mock),
    )


def _blocking_gate(block_id: str, mode: RequestedMode) -> HardGate:
    return HardGate(
        gate_id=f"{block_id}_BLOCKING",
        mode=mode,
        severity=GateSeverity.blocking,
        status=GateStatus.triggered,
        message="test blocking hard gate",
        source_block=block_id,
        basis="test",
    )
