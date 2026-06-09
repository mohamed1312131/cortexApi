from app.schemas import (
    BlockStatus,
    CargoFlags,
    FlagState,
    GateSeverity,
    GateStatus,
    Lane,
    ModeSelection,
    ProviderUsed,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_c_connector import fetch_air_c


def _air_request(
    *,
    origin_city: str | None,
    origin_country: str | None = "FR",
    dangerous_goods: FlagState = FlagState.no,
    temperature_controlled: FlagState = FlagState.no,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-c-connector",
        lane=Lane(
            origin_city=origin_city,
            origin_country=origin_country,
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
        ),
        cargo_flags=CargoFlags(
            dangerous_goods=dangerous_goods,
            temperature_controlled=temperature_controlled,
        ),
    )


def test_air_c_finds_origin_airport_or_city():
    response = fetch_air_c(_air_request(origin_city="Paris", origin_country="FR"))

    assert response.block_id == "AIR-C"
    assert response.mode == RequestedMode.air
    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data.get("airport_code") or response.data.get("airport_name")
    assert response.provenance.provider_used == ProviderUsed.mock


def test_air_c_missing_origin_city_skipped():
    response = fetch_air_c(_air_request(origin_city=None))

    assert response.status == BlockStatus.skipped
    assert "lane.origin_city" in response.missing_fields
    assert response.unknowns


def test_air_c_unknown_airport_returns_unknown_not_clear():
    response = fetch_air_c(
        _air_request(origin_city="NotARealAirport", origin_country="ZZ")
    )

    assert response.status == BlockStatus.unknown
    assert not any(
        gate.severity == GateSeverity.blocking
        and gate.status == GateStatus.triggered
        for gate in response.hard_gates
    )
    assert response.unknowns


def test_air_c_dg_requires_airport_validation_when_not_verified():
    response = fetch_air_c(
        _air_request(
            origin_city="Doha",
            origin_country="QA",
            dangerous_goods=FlagState.yes,
        )
    )

    if response.data and response.data.get("dangerous_goods_handling") not in {
        "yes",
        True,
    }:
        assert any(
            unknown.field == "dangerous_goods_handling"
            for unknown in response.unknowns
        )
