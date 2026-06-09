from app.schemas import (
    BlockStatus,
    CargoFlags,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_b_connector import fetch_air_b


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


def _air_request(cargo_flags: CargoFlags) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-b-connector",
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
        cargo_flags=cargo_flags,
    )


def test_air_b_not_applicable_when_no_special_flags():
    response = fetch_air_b(_air_request(_flags()))

    assert response.status == BlockStatus.not_applicable
    assert response.data == {
        "special_handling_required": False,
        "active_flags": [],
    }
    assert response.confidence.source_confidence == SourceConfidence.authored


def test_air_b_unknown_flag_returns_unknown():
    response = fetch_air_b(
        _air_request(_flags(temperature_controlled=FlagState.unknown))
    )

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.temperature_controlled"
        for unknown in response.unknowns
    )


def test_air_b_temperature_controlled_matches_handling():
    response = fetch_air_b(
        _air_request(_flags(temperature_controlled=FlagState.yes))
    )

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["special_handling_required"] is True
    assert "temperature_controlled" in response.data["active_flags"]
    assert isinstance(response.data["required_handling_codes"], list)


def test_air_b_pharma_or_perishable_matches_category():
    response = fetch_air_b(_air_request(_flags(pharma=FlagState.yes)))

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert (
        response.data["matched_categories"]
        or response.unknowns
    )
