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
from app.services.layer2.connectors.sea_b_connector import fetch_sea_b


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


def _sea_request(
    core_shipment: CoreShipment,
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-b-connector",
        lane=Lane(origin_city="Shanghai", origin_country="CN"),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
        ),
        core_shipment=core_shipment,
        cargo_flags=cargo_flags or _flags(),
    )


def test_sea_b_missing_weight_returns_unknown():
    response = fetch_sea_b(_sea_request(CoreShipment(volume_cbm=12.5)))

    assert response.status == BlockStatus.unknown
    assert "core_shipment.weight_kg" in response.missing_fields
    assert response.unknowns[0].field == "core_shipment.weight_kg"
    assert response.confidence.source_confidence == SourceConfidence.unknown


def test_sea_b_missing_volume_and_dimensions_returns_unknown():
    response = fetch_sea_b(_sea_request(CoreShipment(weight_kg=1200)))

    assert response.status == BlockStatus.unknown
    assert response.data["weight_kg"] == 1200
    assert "core_shipment.volume_cbm" in response.missing_fields
    assert "core_shipment.dimensions" in response.missing_fields
    assert response.unknowns[0].field == "core_shipment.volume_cbm"
    assert response.confidence.source_confidence == SourceConfidence.unknown


def test_sea_b_weight_volume_dimensions_returns_found_or_unknown():
    response = fetch_sea_b(
        _sea_request(
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
    assert response.data["weight_kg"] == 1200
    assert response.data["volume_cbm"] == 12.5
    assert response.data["dimensions_cm"] == [200.0, 150.0, 100.0]
    assert response.data["quantity"] == 4
    assert response.data["packaging"] == "pallets"
    assert response.data["fit_status"] == (
        "planning_only_requires_forwarder_carrier_validation"
    )
    assert isinstance(response.data["candidate_container_examples"], list)
    assert isinstance(response.data["container_spec_count"], int)
    assert response.planning_factors
    assert response.confidence.source_confidence in {
        SourceConfidence.planning_reference,
        SourceConfidence.unknown,
    }


def test_sea_b_dimensions_only_adds_volume_unknown():
    response = fetch_sea_b(
        _sea_request(
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


def test_sea_b_volume_only_adds_dimensions_unknown():
    response = fetch_sea_b(
        _sea_request(
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


def test_sea_b_oversized_adds_unknown():
    response = fetch_sea_b(
        _sea_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
            ),
            cargo_flags=_flags(oversized=FlagState.yes),
        )
    )

    assert response.status == BlockStatus.unknown
    assert "cargo_flags.oversized" in {unknown.field for unknown in response.unknowns}


def test_sea_b_dg_adds_planning_factor():
    response = fetch_sea_b(
        _sea_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
            ),
            cargo_flags=_flags(dangerous_goods=FlagState.yes),
        )
    )

    assert any(
        "Dangerous goods container planning" in factor
        for factor in response.planning_factors
    )


def test_sea_b_does_not_dump_raw_datasets():
    response = fetch_sea_b(
        _sea_request(
            CoreShipment(
                weight_kg=1200,
                volume_cbm=12.5,
                dimensions=[2.0, 1.5, 1.0],
            )
        )
    )

    assert not {
        "records",
        "container_specs",
        "readiness_rules",
        "confidence_rules",
    }.intersection(response.data)
