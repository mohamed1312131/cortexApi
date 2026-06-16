from app.schemas import (
    BlockStatus,
    CargoFlags,
    CoreShipment,
    FlagState,
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.road_b_connector import fetch_road_b


def _flags(**overrides: FlagState) -> CargoFlags:
    values = {
        "dangerous_goods": FlagState.no,
        "temperature_controlled": FlagState.no,
        "oversized": FlagState.no,
        "high_value": FlagState.no,
        "pharma": FlagState.no,
        "food_perishable": FlagState.no,
        "live_animals": FlagState.no,
    }
    values.update(overrides)
    return CargoFlags(**values)


def _road_request(
    core_shipment: CoreShipment,
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-road-b-connector",
        lane=Lane(origin_country="IT", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
        cargo_flags=cargo_flags or _flags(),
        core_shipment=core_shipment,
    )


def test_road_b_missing_weight_returns_unknown():
    response = fetch_road_b(_road_request(CoreShipment(volume_cbm=12.5)))

    assert response.status == BlockStatus.unknown
    assert "core_shipment.weight_kg" in response.missing_fields
    assert response.unknowns[0].field == "core_shipment.weight_kg"
    assert response.confidence.source_confidence == SourceConfidence.unknown


def test_road_b_missing_volume_and_dimensions_returns_unknown():
    response = fetch_road_b(_road_request(CoreShipment(weight_kg=1200)))

    assert response.status == BlockStatus.unknown
    assert response.data["weight_kg"] == 1200
    assert "core_shipment.volume_cbm" in response.missing_fields
    assert "core_shipment.dimensions" in response.missing_fields
    assert response.unknowns[0].field == "core_shipment.volume_cbm"
    assert response.confidence.source_confidence == SourceConfidence.unknown


def test_road_b_weight_volume_dimensions_returns_found_or_unknown():
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
                quantity=4,
                packaging="pallets",
            )
        )
    )

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["fit_status"] == "planning_only_requires_carrier_validation"
    assert response.data["weight_kg"] == 1200
    assert response.data["volume_cbm"] == 12.5
    assert response.data["dimensions_m"] == [2.0, 1.5, 1.0]
    assert isinstance(response.data["candidate_vehicle_examples"], list)
    assert isinstance(response.data["vehicle_profile_count"], int)
    assert response.confidence.source_confidence in {
        SourceConfidence.planning_reference,
        SourceConfidence.unknown,
    }


def test_road_b_dimensions_only_adds_volume_unknown():
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                weight_kg=1200,
                dimensions=[2.0, 1.5, 1.0],
            )
        )
    )

    assert response.status == BlockStatus.unknown
    assert "core_shipment.volume_cbm" in response.missing_fields
    assert "core_shipment.volume_cbm" in {
        unknown.field for unknown in response.unknowns
    }
    assert response.confidence.cap == 0.5


def test_road_b_volume_only_adds_dimensions_unknown():
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
            )
        )
    )

    assert response.status == BlockStatus.unknown
    assert "core_shipment.dimensions" in response.missing_fields
    assert "core_shipment.dimensions" in {
        unknown.field for unknown in response.unknowns
    }
    assert response.confidence.cap == 0.5


def test_road_b_oversized_adds_unknown():
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
            ),
            cargo_flags=_flags(oversized=FlagState.yes),
        )
    )

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert "cargo_flags.oversized" in {unknown.field for unknown in response.unknowns}


def test_road_b_dg_adds_planning_factor():
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
            ),
            cargo_flags=_flags(dangerous_goods=FlagState.yes),
        )
    )

    assert any("ADR vehicle" in factor for factor in response.planning_factors)


def _vaccine_request() -> ValidatedShipmentRequest:
    # Mirrors the SHIP-2C6B299F intake: pharma cold-chain cargo with the other
    # cargo flags still unknown.
    return _road_request(
        CoreShipment(
            cargo_description="vaccines",
            weight_kg=300,
            volume_cbm=2.0,
            packaging="validated cold-chain boxes",
        ),
        cargo_flags=_flags(
            pharma=FlagState.likely,
            temperature_controlled=FlagState.likely,
            dangerous_goods=FlagState.unknown,
            oversized=FlagState.unknown,
            high_value=FlagState.unknown,
            food_perishable=FlagState.unknown,
            live_animals=FlagState.unknown,
        ),
    )


def _chilled_food_request(food_perishable: FlagState) -> ValidatedShipmentRequest:
    return _road_request(
        CoreShipment(
            cargo_description="chilled dairy products",
            weight_kg=800,
            volume_cbm=6.0,
        ),
        cargo_flags=_flags(
            food_perishable=food_perishable,
            temperature_controlled=FlagState.yes,
        ),
    )


def _refrigerated_dg_request(dangerous_goods: FlagState) -> ValidatedShipmentRequest:
    return _road_request(
        CoreShipment(
            cargo_description="refrigerated chemicals",
            weight_kg=900,
            volume_cbm=4.0,
        ),
        cargo_flags=_flags(
            dangerous_goods=dangerous_goods,
            temperature_controlled=FlagState.yes,
        ),
    )


def test_road_b_vaccine_pharma_temp_matches_only_pharma_profile():
    response = fetch_road_b(_vaccine_request())

    assert response.data["matched_vehicle_profile_ids"] == ["ROAD_B_PROFILE_007"]


def test_road_b_vaccine_false_food_dg_profiles_create_no_blocking_gates():
    response = fetch_road_b(_vaccine_request())

    gate_ids = {gate.gate_id for gate in response.hard_gates}
    assert "ROAD_B_ROAD_B_PROFILE_005_HARD_GATE" not in gate_ids
    assert "ROAD_B_ROAD_B_PROFILE_006_HARD_GATE" not in gate_ids
    assert "ROAD_B_ROAD_B_PROFILE_036_HARD_GATE" not in gate_ids

    # No triggered blocking gate: the matched pharma profile is a readiness
    # gate, surfaced at blocking severity with unknown status.
    assert not any(
        gate.status == GateStatus.triggered for gate in response.hard_gates
    )
    pharma_gates = [
        gate
        for gate in response.hard_gates
        if gate.gate_id == "ROAD_B_ROAD_B_PROFILE_007_HARD_GATE"
    ]
    assert len(pharma_gates) == 1
    assert pharma_gates[0].severity == GateSeverity.blocking
    assert pharma_gates[0].status == GateStatus.unknown

    # The evidence gap and the unknown cargo flags stay explicit.
    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "road_b.readiness.ROAD_B_PROFILE_007" in unknown_fields
    assert "cargo_flags.dangerous_goods" in unknown_fields
    assert "cargo_flags.food_perishable" in unknown_fields


def test_road_b_food_chilled_matches_only_when_food_perishable_yes_or_likely():
    for state in (FlagState.yes, FlagState.likely):
        matched = fetch_road_b(_chilled_food_request(state)).data[
            "matched_vehicle_profile_ids"
        ]
        assert "ROAD_B_PROFILE_005" in matched

    for state in (FlagState.unknown, FlagState.no):
        matched = fetch_road_b(_chilled_food_request(state)).data[
            "matched_vehicle_profile_ids"
        ]
        assert "ROAD_B_PROFILE_005" not in matched
        assert "ROAD_B_PROFILE_006" not in matched


def test_road_b_refrigerated_dg_matches_only_when_dangerous_goods_yes_or_likely():
    for state in (FlagState.yes, FlagState.likely):
        matched = fetch_road_b(_refrigerated_dg_request(state)).data[
            "matched_vehicle_profile_ids"
        ]
        assert "ROAD_B_PROFILE_036" in matched

    for state in (FlagState.unknown, FlagState.no):
        matched = fetch_road_b(_refrigerated_dg_request(state)).data[
            "matched_vehicle_profile_ids"
        ]
        assert "ROAD_B_PROFILE_036" not in matched


def test_road_b_oversized_profiles_keep_triggered_blocking_gates():
    # Abnormal-movement constraints genuinely apply once oversized is yes:
    # those hard gates must stay triggered (not weakened to unknown).
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                cargo_description="wind turbine blade",
                weight_kg=30000,
                volume_cbm=120.0,
                dimensions=[60.0, 4.0, 4.0],
            ),
            cargo_flags=_flags(oversized=FlagState.yes),
        )
    )

    triggered_blocking = [
        gate
        for gate in response.hard_gates
        if gate.status == GateStatus.triggered
        and gate.severity == GateSeverity.blocking
    ]
    assert triggered_blocking


def test_road_b_does_not_dump_raw_datasets():
    response = fetch_road_b(
        _road_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
            )
        )
    )

    assert not {
        "records",
        "vehicle_profiles",
        "standard_limits",
        "abnormal_load_rules",
        "confidence_rules",
    }.intersection(response.data)
