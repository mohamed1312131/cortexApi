from app.schemas import (
    BlockStatus,
    CargoFlags,
    Commercial,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_f_connector import fetch_air_f


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
    cargo_flags: CargoFlags | None = None,
    incoterm: str | None = "DAP",
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-f-connector",
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
        commercial=Commercial(incoterm=incoterm),
    )


def test_air_f_missing_country_returns_unknown():
    response = fetch_air_f(
        _air_request(origin_country=None, destination_country="US")
    )

    assert response.status == BlockStatus.unknown
    assert "lane.origin_country" in response.missing_fields
    assert any(
        unknown.field == "lane.origin_country" for unknown in response.unknowns
    )


def test_air_f_basic_air_shipment_returns_found_or_unknown():
    response = fetch_air_f(_air_request())

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["border_status"] == (
        "planning_only_requires_forwarder_customs_validation"
    )
    assert isinstance(response.data["required_documents"], list)


def test_air_f_unknown_special_flag_adds_unknown():
    response = fetch_air_f(
        _air_request(cargo_flags=_flags(pharma=FlagState.unknown))
    )

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.pharma" for unknown in response.unknowns
    )


def test_air_f_missing_incoterm_adds_unknown():
    response = fetch_air_f(_air_request(incoterm=None))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "commercial.incoterm"
        for unknown in response.unknowns
    )
