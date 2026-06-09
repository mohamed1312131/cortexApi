from app.schemas import (
    BlockStatus,
    CargoFlags,
    Commercial,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.sea_d_connector import fetch_sea_d


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
    *,
    origin_country: str | None = "CN",
    destination_country: str | None = "FR",
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-d-connector",
        lane=Lane(
            origin_city="Shanghai",
            origin_country=origin_country,
            destination_city="Marseille",
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
            needs_mode_selection=False,
        ),
        cargo_flags=cargo_flags or _flags(),
        core_shipment=CoreShipment(
            cargo_description="machinery parts",
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(incoterm="FOB"),
    )


def test_sea_d_basic_sea_shipment_returns_found_or_unknown():
    response = fetch_sea_d(_sea_request())

    assert response.block_id == "SEA-D"
    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert "carrier_trade_lane_status" in response.data
    assert isinstance(response.data["carrier_examples"], list)
    assert isinstance(response.data["trade_lane_examples"], list)
    assert isinstance(response.data["reference_carrier_count"], int)


def test_sea_d_missing_country_adds_unknown():
    response = fetch_sea_d(_sea_request(origin_country=None))

    assert "lane.origin_country" in response.missing_fields
    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "lane.origin_country" in unknown_fields


def test_sea_d_unknown_special_flag_adds_unknown():
    response = fetch_sea_d(
        _sea_request(cargo_flags=_flags(pharma=FlagState.unknown))
    )

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "cargo_flags.pharma" in unknown_fields
