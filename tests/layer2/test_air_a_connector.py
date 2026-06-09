from app.schemas import (
    BlockStatus,
    CargoFlags,
    FlagState,
    Lane,
    ModeSelection,
    ProviderUsed,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_a_connector import fetch_air_a


def _air_request(
    *,
    dangerous_goods: FlagState,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-air-a-connector",
        lane=Lane(
            origin_city="Shanghai",
            origin_country="CN",
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        profiles=profiles,
    )


def test_air_a_not_applicable_when_not_dg():
    response = fetch_air_a(_air_request(dangerous_goods=FlagState.no))

    assert response.status == BlockStatus.not_applicable
    assert response.data == {"dangerous_goods": "no"}
    assert response.confidence.source_confidence == SourceConfidence.authored


def test_air_a_unknown_when_dg_status_unknown():
    response = fetch_air_a(_air_request(dangerous_goods=FlagState.unknown))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.dangerous_goods"
        for unknown in response.unknowns
    )


def test_air_a_missing_un_number_unknown():
    response = fetch_air_a(_air_request(dangerous_goods=FlagState.yes))

    assert response.status == BlockStatus.unknown
    assert "profiles.dangerous_goods.un_number" in response.missing_fields
    assert any(
        unknown.field == "profiles.dangerous_goods.un_number"
        for unknown in response.unknowns
    )


def test_air_a_un3480_found():
    response = fetch_air_a(
        _air_request(dangerous_goods=FlagState.yes, un_number="UN3480")
    )

    assert response.status == BlockStatus.found
    assert response.data["identification_number"] == "UN3480"
    assert response.planning_factors
    assert response.provenance.provider_used == ProviderUsed.mock


def test_air_a_numeric_un_normalizes():
    response = fetch_air_a(
        _air_request(dangerous_goods=FlagState.yes, un_number="3480")
    )

    assert response.status == BlockStatus.found
    assert response.data["identification_number"] == "UN3480"
