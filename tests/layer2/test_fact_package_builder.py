from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    CompletenessStatus,
    FetchPlan,
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    ProviderUsed,
    Provenance,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.fact_package_builder import build_fact_package
from app.services.layer2.fetch_executor import execute_fetch_plan
from app.services.layer2.fetch_planner import build_fetch_plan


def _road_request(origin_country: str, destination_country: str) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=f"case-{origin_country}-{destination_country}",
        lane=Lane(
            origin_country=origin_country,
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
    )


def _package_for_lane(origin_country: str, destination_country: str):
    request = _road_request(origin_country, destination_country)
    plan = build_fetch_plan(request)
    responses = execute_fetch_plan(request, plan)
    return request, build_fact_package(request, plan, responses)


def test_rollup_includes_road_c_blocking_gate():
    request, package = _package_for_lane("CN", "FR")

    assert package.case_id == request.case_id
    assert package.completeness.status == CompletenessStatus.blocked
    assert package.derived_rollup.hard_gates
    assert (
        package.derived_rollup.hard_gates[0].gate_id
        == "ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL"
    )
    assert "ROAD-C" in package.derived_rollup.blocks_called
    assert RequestedMode.road in package.derived_rollup.modes_covered


def test_rollup_has_no_blocking_gate_for_it_fr():
    _, package = _package_for_lane("IT", "FR")

    assert package.completeness.status in {
        CompletenessStatus.complete_enough,
        CompletenessStatus.incomplete_but_usable,
    }
    assert not any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in package.derived_rollup.hard_gates
    )


def test_rollup_includes_unknown_for_missing_pair():
    _, package = _package_for_lane("XX", "YY")

    assert package.completeness.status == CompletenessStatus.incomplete_but_usable
    assert package.derived_rollup.unknowns
    assert "ROAD-C" in package.derived_rollup.blocks_empty


def test_rollup_marks_failed_block():
    request = _road_request("IT", "FR")
    response = BlockResponse(
        block_id="BROKEN-BLOCK",
        mode=RequestedMode.road,
        status=BlockStatus.error,
        confidence=BlockConfidence(source_confidence=SourceConfidence.unknown),
        provenance=Provenance(source="test", provider_used=ProviderUsed.mock),
    )
    plan = FetchPlan(case_id=request.case_id)

    package = build_fact_package(request, plan, [response])

    assert package.completeness.status == CompletenessStatus.insufficient
    assert "BROKEN-BLOCK" in package.derived_rollup.blocks_failed


def test_rollup_includes_confidence_cap_from_block():
    request = _road_request("IT", "FR")
    response = BlockResponse(
        block_id="CAP-BLOCK",
        mode=RequestedMode.road,
        status=BlockStatus.found,
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.authored,
            cap=0.3,
            reasons=["test cap"],
        ),
        provenance=Provenance(source="test", provider_used=ProviderUsed.mock),
    )
    plan = FetchPlan(case_id=request.case_id)

    package = build_fact_package(request, plan, [response])

    assert len(package.derived_rollup.confidence_caps) == 1
    cap = package.derived_rollup.confidence_caps[0]
    assert cap.cap == 0.3
    assert cap.source_block == "CAP-BLOCK"
    assert cap.reasons == ["test cap"]
