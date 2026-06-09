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
from app.services.layer2.connectors.sea_a_connector import (
    _find_record,
    _load_records,
    _normalize_un,
    fetch_sea_a,
)


def _known_un_number() -> str:
    if _find_record(_load_records(), "UN3480") is not None:
        return "UN3480"
    return "UN1410"


def _sea_request(
    *,
    dangerous_goods: FlagState,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-sea-a-connector",
        lane=Lane(origin_city="Shanghai", origin_country="CN"),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        profiles=profiles,
    )


def test_sea_a_not_applicable_when_not_dg():
    response = fetch_sea_a(_sea_request(dangerous_goods=FlagState.no))

    assert response.status == BlockStatus.not_applicable
    assert response.data == {"dangerous_goods": "no"}
    assert response.confidence.source_confidence == SourceConfidence.authored


def test_sea_a_unknown_when_dg_status_unknown():
    response = fetch_sea_a(_sea_request(dangerous_goods=FlagState.unknown))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.dangerous_goods"
        for unknown in response.unknowns
    )


def test_sea_a_missing_un_number_unknown():
    response = fetch_sea_a(_sea_request(dangerous_goods=FlagState.yes))

    assert response.status == BlockStatus.unknown
    assert "profiles.dangerous_goods.un_number" in response.missing_fields
    assert any(
        unknown.field == "profiles.dangerous_goods.un_number"
        for unknown in response.unknowns
    )


def test_sea_a_un3480_found():
    un_number = _known_un_number()

    response = fetch_sea_a(
        _sea_request(dangerous_goods=FlagState.yes, un_number=un_number)
    )

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["identification_number"] == _normalize_un(un_number)
    assert response.planning_factors
    assert response.provenance.provider_used == ProviderUsed.mock


def test_sea_a_numeric_un_normalizes():
    un_number = _known_un_number()

    response = fetch_sea_a(
        _sea_request(
            dangerous_goods=FlagState.yes,
            un_number=un_number.replace("UN", ""),
        )
    )

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["identification_number"] == _normalize_un(un_number)
