from app.schemas import (
    BlockStatus,
    CargoFlags,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_h_connector import fetch_air_h


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


def _air_request(
    *,
    origin_country: str | None = "FR",
    destination_country: str | None = "US",
    cargo_description: str | None = "electronics spare parts",
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-h-connector",
        lane=Lane(
            origin_city="Paris",
            origin_country=origin_country,
            destination_city="New York",
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
        ),
        cargo_flags=cargo_flags or _flags(),
        core_shipment=CoreShipment(cargo_description=cargo_description),
    )


def test_air_h_missing_country_returns_unknown():
    response = fetch_air_h(
        _air_request(origin_country=None, destination_country="US")
    )

    assert response.status == BlockStatus.unknown
    assert "lane.origin_country" in response.missing_fields
    assert any(
        unknown.field == "lane.origin_country" for unknown in response.unknowns
    )


def test_air_h_basic_air_shipment_returns_found_or_unknown():
    response = fetch_air_h(_air_request())

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["security_status"] == (
        "planning_only_requires_airline_forwarder_security_validation"
    )
    assert isinstance(response.data["required_security_actions"], list)
    assert isinstance(response.data["placi_required_elements"], list)


def test_air_h_missing_cargo_description_adds_unknown():
    response = fetch_air_h(_air_request(cargo_description=None))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "core_shipment.cargo_description"
        for unknown in response.unknowns
    )


def test_air_h_unknown_special_flag_adds_unknown():
    response = fetch_air_h(_air_request(cargo_flags=_flags(high_value=FlagState.unknown)))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.high_value"
        for unknown in response.unknowns
    )
