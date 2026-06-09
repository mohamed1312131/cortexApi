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
from app.services.layer2.connectors.air_i_connector import fetch_air_i


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
    origin_city: str | None = "Paris",
    destination_city: str | None = "New York",
    ready_date: str | None = "2026-06-10",
    deadline: str | None = "2026-06-12",
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-i-connector",
        lane=Lane(
            origin_city=origin_city,
            origin_country=origin_country,
            destination_city=destination_city,
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
        ),
        cargo_flags=cargo_flags or _flags(),
        commercial=Commercial(ready_date=ready_date, deadline=deadline),
    )


def test_air_i_missing_country_returns_unknown():
    response = fetch_air_i(
        _air_request(origin_country=None, destination_country="US")
    )

    assert response.status == BlockStatus.unknown
    assert "lane.origin_country" in response.missing_fields
    assert any(
        unknown.field == "lane.origin_country" for unknown in response.unknowns
    )


def test_air_i_basic_air_route_returns_found_or_unknown():
    response = fetch_air_i(_air_request())

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["route_status"] == (
        "planning_only_requires_forwarder_airline_schedule_validation"
    )
    assert isinstance(response.data["tracking_milestones"], list)


def test_air_i_missing_ready_date_and_deadline_adds_unknowns():
    response = fetch_air_i(_air_request(ready_date=None, deadline=None))
    fields = {unknown.field for unknown in response.unknowns}

    assert response.status == BlockStatus.unknown
    assert "commercial.ready_date" in fields
    assert "commercial.deadline" in fields


def test_air_i_unknown_special_flag_adds_unknown():
    response = fetch_air_i(_air_request(cargo_flags=_flags(high_value=FlagState.unknown)))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.high_value"
        for unknown in response.unknowns
    )
