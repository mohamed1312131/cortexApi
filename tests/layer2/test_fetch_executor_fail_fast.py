from app.schemas import (
    BlockStatus,
    CargoFlags,
    Commercial,
    CompletenessStatus,
    CoreShipment,
    FlagState,
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.service import build_fact_package_for_request


def _road_request(
    *,
    case_id: str,
    origin_country: str,
    destination_country: str,
    dangerous_goods: FlagState = FlagState.no,
    weight_kg: float | None = 1200,
    volume_cbm: float | None = 12.5,
    dimensions: list[float] | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id=case_id,
        lane=Lane(
            origin_country=origin_country,
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(
            dangerous_goods=dangerous_goods,
            temperature_controlled=FlagState.no,
            oversized=FlagState.no,
            high_value=FlagState.no,
            pharma=FlagState.no,
            food_perishable=FlagState.no,
            live_animals=FlagState.no,
        ),
        core_shipment=CoreShipment(
            weight_kg=weight_kg,
            volume_cbm=volume_cbm,
            dimensions=dimensions or [1.0, 1.0, 1.0],
        ),
        commercial=Commercial(
            incoterm="DAP",
            ready_date="2026-06-10",
            deadline="2026-06-12",
        ),
    )


def test_cn_fr_road_service_skips_deeper_blocks_after_road_c_gate():
    request = _road_request(
        case_id="case-road-cn-fr-fail-fast",
        origin_country="CN",
        destination_country="FR",
    )

    package = build_fact_package_for_request(request)

    assert [item.block_id for item in package.fetch_plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert package.block_responses[0].block_id == "ROAD-C"
    assert package.block_responses[0].status == BlockStatus.found
    assert any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in package.block_responses[0].hard_gates
    )
    assert [response.status for response in package.block_responses[1:]] == [
        BlockStatus.skipped,
        BlockStatus.skipped,
        BlockStatus.skipped,
        BlockStatus.skipped,
    ]
    assert package.completeness.status == CompletenessStatus.blocked


def test_it_fr_road_service_executes_all_road_blocks_without_road_c_gate():
    request = _road_request(
        case_id="case-road-it-fr-all-road-blocks",
        origin_country="IT",
        destination_country="FR",
    )

    package = build_fact_package_for_request(request)

    assert [item.block_id for item in package.fetch_plan.items] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert [response.block_id for response in package.block_responses] == [
        "ROAD-C",
        "ROAD-A",
        "ROAD-B",
        "ROAD-F",
        "ROAD-COST",
    ]
    assert package.block_responses[0].status == BlockStatus.found
    assert not any(
        gate.source_block == "ROAD-C"
        and gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in package.derived_rollup.hard_gates
    )
    assert package.block_responses[1].status == BlockStatus.not_applicable
    assert package.block_responses[2].status == BlockStatus.found
    assert package.block_responses[3].status == BlockStatus.found
