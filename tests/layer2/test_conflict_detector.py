from app.schemas import (
    BlockResponse,
    BlockStatus,
    FetchPlan,
    GateSeverity,
    GateStatus,
    HardGate,
    Lane,
    ModeSelection,
    ProviderUsed,
    Provenance,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.conflict_detector import detect_conflicts
from app.services.layer2.fact_package_builder import build_fact_package
from app.services.layer2.service import build_fact_package_for_request


def _road_request(origin_country: str, destination_country: str) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=f"case-conflicts-{origin_country.lower()}-{destination_country.lower()}",
        lane=Lane(origin_country=origin_country, destination_country=destination_country),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
        ),
    )


def _response(
    block_id: str,
    mode: RequestedMode,
    status: BlockStatus,
    hard_gates: list[HardGate] | None = None,
) -> BlockResponse:
    return BlockResponse(
        block_id=block_id,
        mode=mode,
        status=status,
        hard_gates=hard_gates or [],
        provenance=Provenance(source="test", provider_used=ProviderUsed.mock),
    )


def _blocking_gate(block_id: str, mode: RequestedMode) -> HardGate:
    return HardGate(
        gate_id=f"{block_id}_BLOCKING",
        mode=mode,
        severity=GateSeverity.blocking,
        status=GateStatus.triggered,
        message="test blocking gate",
        source_block=block_id,
        basis="test",
    )


def _block_response(package, block_id: str):
    for response in package.block_responses:
        if response.block_id == block_id:
            return response
    raise AssertionError(f"Expected block response {block_id}")


def test_no_conflicts_for_normal_road_it_fr():
    package = build_fact_package_for_request(_road_request("IT", "FR"))

    assert not [
        conflict
        for conflict in package.conflicts
        if conflict.type == "mode_blocked_but_later_blocks_present"
    ]


def test_road_blocks_run_even_after_cn_fr_road_c_block():
    # Cascade-skip removed: a blocking corridor gate no longer suppresses the
    # deeper road blocks, so the worker still gets a complete report.
    package = build_fact_package_for_request(_road_request("CN", "FR"))

    road_c = _block_response(package, "ROAD-C")
    assert any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in road_c.hard_gates
    )

    present = {response.block_id for response in package.block_responses}
    assert {"ROAD-B", "ROAD-F", "ROAD-COST"}.issubset(present)
    assert not [
        response
        for response in package.block_responses
        if response.block_id in {"ROAD-B", "ROAD-F", "ROAD-COST"}
        and response.status == BlockStatus.skipped
    ]
    # the old skip-enforcing policy conflicts are retired
    assert not [
        conflict
        for conflict in package.conflicts
        if conflict.type
        in {
            "mode_blocked_but_later_blocks_present",
            "cost_reference_present_for_blocked_mode",
        }
    ]


def test_later_block_after_blocking_gate_is_no_longer_a_conflict():
    responses = [
        _response(
            "ROAD-C",
            RequestedMode.road,
            BlockStatus.found,
            hard_gates=[_blocking_gate("ROAD-C", RequestedMode.road)],
        ),
        _response("ROAD-B", RequestedMode.road, BlockStatus.found),
    ]

    conflicts = detect_conflicts(responses)

    # deeper blocks running after a gate is the intended behavior now
    assert "mode_blocked_but_later_blocks_present" not in {
        conflict.type for conflict in conflicts
    }


def test_cost_for_blocked_mode_is_no_longer_a_conflict():
    responses = [
        _response(
            "ROAD-C",
            RequestedMode.road,
            BlockStatus.found,
            hard_gates=[_blocking_gate("ROAD-C", RequestedMode.road)],
        ),
        _response("ROAD-COST", RequestedMode.road, BlockStatus.found),
    ]

    conflicts = detect_conflicts(responses)

    assert "cost_reference_present_for_blocked_mode" not in {
        conflict.type for conflict in conflicts
    }


def test_conflict_detector_catches_duplicate_block_response():
    responses = [
        _response("SEA-B", RequestedMode.sea, BlockStatus.found),
        _response("SEA-B", RequestedMode.sea, BlockStatus.unknown),
    ]

    conflicts = detect_conflicts(responses)

    assert "duplicate_block_response" in {conflict.type for conflict in conflicts}


def test_fact_package_builder_includes_conflicts():
    request = _road_request("CN", "FR")
    # duplicate block responses still surface as a conflict through the builder
    responses = [
        _response("ROAD-B", RequestedMode.road, BlockStatus.found),
        _response("ROAD-B", RequestedMode.road, BlockStatus.unknown),
    ]

    package = build_fact_package(
        request,
        FetchPlan(case_id=request.case_id),
        responses,
    )

    assert "duplicate_block_response" in {
        conflict.type for conflict in package.conflicts
    }
