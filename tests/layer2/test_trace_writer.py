import json

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
from app.services.layer2.connectors.road_c_connector import fetch_road_c
from app.services.layer2.fact_package_builder import build_fact_package
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.trace_writer import (
    build_layer2_trace,
    summarize_block_response,
    write_layer2_trace_json,
)


def _road_request(origin_country: str, destination_country: str) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=f"case-trace-{origin_country.lower()}-{destination_country.lower()}",
        lane=Lane(
            origin_city="Milan" if origin_country == "IT" else "Shenzhen",
            destination_city="Paris",
            origin_country=origin_country,
            destination_country=destination_country,
        ),
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


def test_summarize_block_response_excludes_data():
    response = fetch_road_c("IT", "FR")

    summary = summarize_block_response(response)

    assert "data" not in summary
    assert summary["block_id"] == "ROAD-C"
    assert summary["status"] == response.status
    assert summary["provider_used"] == ProviderUsed.mock


def test_build_layer2_trace_for_road_it_fr():
    package = build_fact_package_for_request(_road_request("IT", "FR"))

    trace = build_layer2_trace(package)

    assert trace["case_id"] == package.case_id
    assert "planned_blocks" in trace
    assert "block_summaries" in trace
    assert trace["hard_gate_count"] == 0


def test_build_layer2_trace_for_blocked_cn_fr():
    package = build_fact_package_for_request(_road_request("CN", "FR"))

    trace = build_layer2_trace(package)

    assert trace["hard_gate_count"] >= 1
    assert "ROAD-C" in trace["planned_blocks"] or "ROAD-C" in trace["called_blocks"]
    road_cost = next(
        (
            response
            for response in package.block_responses
            if response.block_id == "ROAD-COST"
        ),
        None,
    )
    if road_cost is None:
        assert "ROAD-COST" not in trace["called_blocks"]
    else:
        assert road_cost.status == BlockStatus.skipped


def test_trace_includes_conflict_count():
    request = _road_request("CN", "FR")
    responses = [
        _response(
            "ROAD-C",
            RequestedMode.road,
            BlockStatus.found,
            hard_gates=[_blocking_gate("ROAD-C", RequestedMode.road)],
        ),
        _response("ROAD-B", RequestedMode.road, BlockStatus.found),
    ]
    package = build_fact_package(request, FetchPlan(case_id=request.case_id), responses)

    trace = build_layer2_trace(package)

    assert trace["conflict_count"] > 0
    assert "mode_blocked_but_later_blocks_present" in trace["conflict_types"]


def test_write_layer2_trace_json(tmp_path):
    package = build_fact_package_for_request(_road_request("IT", "FR"))

    output_path = write_layer2_trace_json(package, tmp_path / "layer2-trace.json")

    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["case_id"] == package.case_id
    assert all("data" not in summary for summary in payload["block_summaries"])
