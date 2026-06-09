from app.schemas import (
    BlockStatus,
    CargoFlags,
    FlagState,
    GateSeverity,
    Lane,
    ModeSelection,
    ProviderUsed,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.sea_c_connector import fetch_sea_c


def _sea_request(
    *,
    origin_city: str | None,
    origin_country: str | None,
    dangerous_goods: FlagState = FlagState.unknown,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-c-connector",
        lane=Lane(
            origin_city=origin_city,
            origin_country=origin_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
    )


def test_sea_c_finds_major_port():
    request = _sea_request(origin_city="Shanghai", origin_country="CN")

    response = fetch_sea_c(request)

    assert response.block_id == "SEA-C"
    assert response.mode == RequestedMode.sea
    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["country_iso2"] == "CN"
    assert "cap_container" in response.data
    assert response.provenance.provider_used == ProviderUsed.mock


def test_sea_c_missing_origin_city_skipped():
    request = _sea_request(origin_city=None, origin_country="CN")

    response = fetch_sea_c(request)

    assert response.status == BlockStatus.skipped
    assert "lane.origin_city" in response.missing_fields
    assert response.unknowns


def test_sea_c_unknown_port_returns_unknown_not_clear():
    request = _sea_request(origin_city="NotARealPort", origin_country="ZZ")

    response = fetch_sea_c(request)

    assert response.status == BlockStatus.unknown
    assert not any(
        gate.severity == GateSeverity.blocking for gate in response.hard_gates
    )
    assert response.unknowns


def test_sea_c_dg_requires_terminal_validation_when_not_verified():
    request = _sea_request(
        origin_city="Shanghai",
        origin_country="CN",
        dangerous_goods=FlagState.yes,
    )

    response = fetch_sea_c(request)

    if response.data.get("cap_dg_handling") != "yes":
        assert any(unknown.field == "cap_dg_handling" for unknown in response.unknowns)
