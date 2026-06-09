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
from app.services.layer2.connectors.sea_i_connector import fetch_sea_i


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
    ready_date: str | None = "2026-06-10",
    deadline: str | None = "2026-07-10",
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-i-connector",
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
        commercial=Commercial(
            incoterm="FOB",
            ready_date=ready_date,
            deadline=deadline,
        ),
    )


def test_sea_i_missing_country_returns_unknown():
    response = fetch_sea_i(_sea_request(origin_country=None))

    assert response.status == BlockStatus.unknown
    assert "lane.origin_country" in response.missing_fields
    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "lane.origin_country" in unknown_fields


def test_sea_i_basic_sea_route_returns_found_or_unknown():
    response = fetch_sea_i(_sea_request())

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert "schedule_status" in response.data
    assert isinstance(response.data["chokepoint_examples"], list)


def test_sea_i_missing_ready_date_and_deadline_adds_unknowns():
    response = fetch_sea_i(_sea_request(ready_date=None, deadline=None))

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "commercial.ready_date" in unknown_fields
    assert "commercial.deadline" in unknown_fields


def test_sea_i_unknown_special_flag_adds_unknown():
    response = fetch_sea_i(
        _sea_request(cargo_flags=_flags(live_animals=FlagState.unknown))
    )

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "cargo_flags.live_animals" in unknown_fields
