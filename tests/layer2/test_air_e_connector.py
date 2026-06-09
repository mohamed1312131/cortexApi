from app.schemas import (
    BlockStatus,
    CargoFlags,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_e_connector import fetch_air_e


def _air_request(
    *,
    weight_kg: float | None = 1200,
    volume_cbm: float | None = 4.5,
    dimensions: list[float] | None = None,
    oversized: FlagState = FlagState.no,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-e-connector",
        lane=Lane(
            origin_city="Paris",
            origin_country="FR",
            destination_city="New York",
            destination_country="US",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
        ),
        cargo_flags=CargoFlags(oversized=oversized),
        core_shipment=CoreShipment(
            weight_kg=weight_kg,
            volume_cbm=volume_cbm,
            dimensions=dimensions,
            quantity=2,
            packaging="crates",
        ),
    )


def test_air_e_missing_weight_returns_unknown():
    response = fetch_air_e(_air_request(weight_kg=None))

    assert response.status == BlockStatus.unknown
    assert "core_shipment.weight_kg" in response.missing_fields
    assert response.unknowns[0].field == "core_shipment.weight_kg"
    assert response.confidence.source_confidence == SourceConfidence.unknown


def test_air_e_missing_volume_and_dimensions_returns_unknown():
    response = fetch_air_e(
        _air_request(
            volume_cbm=None,
            dimensions=None,
        )
    )

    assert response.status == BlockStatus.unknown
    assert response.data["weight_kg"] == 1200
    assert "core_shipment.volume_cbm" in response.missing_fields
    assert "core_shipment.dimensions" in response.missing_fields
    assert {unknown.field for unknown in response.unknowns} == {
        "core_shipment.volume_cbm",
        "core_shipment.dimensions",
    }


def test_air_e_weight_volume_dimensions_returns_found():
    response = fetch_air_e(
        _air_request(
            volume_cbm=4.5,
            dimensions=[1.2, 0.8, 0.7],
        )
    )

    assert response.status == BlockStatus.found
    assert response.data["fit_assessment"] == (
        "planning_only_requires_airline_forwarder_validation"
    )
    assert response.data["reference_aircraft_count"] > 0
    assert response.data["reference_uld_count"] > 0
    assert response.data["possible_uld_families"]
    assert response.data["max_piece_length_cm"] == 120
    assert response.confidence.source_confidence == (
        SourceConfidence.planning_reference
    )
    assert response.planning_factors


def test_air_e_oversized_adds_unknown():
    response = fetch_air_e(
        _air_request(
            volume_cbm=4.5,
            dimensions=[2.2, 1.6, 1.4],
            oversized=FlagState.yes,
        )
    )

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.oversized"
        for unknown in response.unknowns
    )
    assert any("Oversized cargo" in factor for factor in response.planning_factors)
