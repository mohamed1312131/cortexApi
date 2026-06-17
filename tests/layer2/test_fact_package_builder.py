from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    CargoFlags,
    Commercial,
    CompletenessStatus,
    CoreShipment,
    FetchPlan,
    FlagState,
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
from app.services.layer2.service import build_fact_package_for_request


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


def _block_response(package, block_id: str):
    for response in package.block_responses:
        if response.block_id == block_id:
            return response
    raise AssertionError(f"Expected block response {block_id}")


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


def test_real_sea_i_blocking_gate_still_runs_sea_cost():
    package = build_fact_package_for_request(_sea_dg_request())

    sea_i = _block_response(package, "SEA-I")
    sea_cost = _block_response(package, "SEA-COST")

    assert sea_i.status == BlockStatus.found
    assert any(
        gate.gate_id == "SEA_I_CUTOFF_DG_DOCUMENTS_HARD_GATE"
        and gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in sea_i.hard_gates
    )
    # cascade-skip removed: SEA-COST runs and contributes its planning reference
    # alongside the blocking gate, instead of being suppressed.
    assert sea_cost.status != BlockStatus.skipped
    assert any(
        gate.gate_id == "SEA_I_CUTOFF_DG_DOCUMENTS_HARD_GATE"
        for gate in package.derived_rollup.hard_gates
    )
    assert "mode_blocked_but_later_blocks_present" not in {
        conflict.type for conflict in package.conflicts
    }
    assert "cost_reference_present_for_blocked_mode" not in {
        conflict.type for conflict in package.conflicts
    }


def _sea_dg_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-i-cost-regression",
        lane=Lane(
            origin_city="Shanghai",
            origin_country="CN",
            destination_city="Marseille",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(dangerous_goods=FlagState.yes),
        core_shipment=CoreShipment(
            cargo_description="dangerous goods",
            weight_kg=8000,
            volume_cbm=20,
            dimensions=[2.0, 1.0, 1.0],
        ),
        commercial=Commercial(
            incoterm="FOB",
            ready_date="2026-06-10",
            deadline="2026-07-10",
        ),
        profiles={"dangerous_goods": {"un_number": "UN1410"}},
    )


def _tunis_berlin_vaccine_request(
    destination_country: str | None,
) -> ValidatedShipmentRequest:
    # Mirrors the SHIP-2C6B299F intake: pharma cold-chain cargo, other flags
    # unknown, all three candidate modes.
    return ValidatedShipmentRequest(
        case_id="case-tunis-berlin-vaccine",
        lane=Lane(
            origin_city="Tunis",
            origin_country="TN",
            destination_city="Berlin",
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.unknown,
            candidate_modes=[
                RequestedMode.sea,
                RequestedMode.air,
                RequestedMode.road,
            ],
            needs_mode_selection=True,
        ),
        cargo_flags=CargoFlags(
            dangerous_goods=FlagState.unknown,
            temperature_controlled=FlagState.likely,
            oversized=FlagState.unknown,
            high_value=FlagState.unknown,
            pharma=FlagState.likely,
            food_perishable=FlagState.unknown,
            live_animals=FlagState.unknown,
        ),
        core_shipment=CoreShipment(
            cargo_description="vaccines",
            weight_kg=300,
            volume_cbm=2,
            packaging="validated cold-chain boxes",
        ),
        commercial=Commercial(ready_date="tomorrow"),
        active_profiles=["pharma", "temperature_controlled"],
    )


def test_tunis_berlin_vaccine_package_not_blocked_by_road_b_false_positives():
    # Destination country unresolved (the original SHIP-2C6B299F shape): the
    # only blocking gates in this package used to be ROAD-B false positives
    # (food_chilled / food_frozen / refrigerated_dg_or_chemical).
    package = build_fact_package_for_request(_tunis_berlin_vaccine_request(None))

    gate_ids = {gate.gate_id for gate in package.derived_rollup.hard_gates}
    assert "ROAD_B_ROAD_B_PROFILE_005_HARD_GATE" not in gate_ids
    assert "ROAD_B_ROAD_B_PROFILE_006_HARD_GATE" not in gate_ids
    assert "ROAD_B_ROAD_B_PROFILE_036_HARD_GATE" not in gate_ids

    assert package.completeness.status != CompletenessStatus.blocked
    assert package.completeness.status == CompletenessStatus.incomplete_but_usable

    # The true pharma readiness gate stays visible, explicit and unverified.
    pharma_gates = [
        gate
        for gate in package.derived_rollup.hard_gates
        if gate.gate_id == "ROAD_B_ROAD_B_PROFILE_007_HARD_GATE"
    ]
    assert len(pharma_gates) == 1
    assert pharma_gates[0].severity == GateSeverity.blocking
    assert pharma_gates[0].status == GateStatus.unknown
    assert "road_b.readiness.ROAD_B_PROFILE_007" in {
        unknown.field for unknown in package.derived_rollup.unknowns
    }


def test_tunis_berlin_vaccine_with_de_resolved_road_b_never_blocks():
    # With Berlin resolved to DE the road corridor block runs; its authored
    # ferry/bilateral gate may legitimately block, but no triggered blocking
    # gate may come from ROAD-B.
    package = build_fact_package_for_request(_tunis_berlin_vaccine_request("DE"))

    road_b_triggered_blocking = [
        gate
        for gate in package.derived_rollup.hard_gates
        if gate.source_block == "ROAD-B"
        and gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
    ]
    assert road_b_triggered_blocking == []
