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
from app.services.layer2.connectors.air_d_connector import fetch_air_d


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
    cargo_flags: CargoFlags | None = None,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-air-d-connector",
        lane=Lane(
            origin_city="Paris",
            origin_country="FR",
            destination_city="New York",
            destination_country="US",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
            needs_mode_selection=False,
        ),
        cargo_flags=cargo_flags or _flags(),
        core_shipment=CoreShipment(
            cargo_description="electronics spare parts",
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
        ),
        profiles=profiles,
    )


def test_air_d_basic_air_shipment_returns_found_or_unknown():
    response = fetch_air_d(_air_request())

    assert response.block_id == "AIR-D"
    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert "carrier_capability_status" in response.data
    assert isinstance(response.data["carrier_examples"], list)
    assert isinstance(response.data["reference_carrier_count"], int)


def test_air_d_unknown_special_flag_adds_unknown():
    response = fetch_air_d(
        _air_request(cargo_flags=_flags(high_value=FlagState.unknown))
    )

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "cargo_flags.high_value" in unknown_fields


def test_air_d_dg_requires_carrier_validation_when_not_verified():
    response = fetch_air_d(
        _air_request(
            cargo_flags=_flags(dangerous_goods=FlagState.yes),
            un_number="UN3480",
        )
    )

    assert any(
        "airline/forwarder confirms acceptance" in factor
        for factor in response.planning_factors
    )

    has_clear_dg_example = any(
        str(example.get("dangerous_goods_acceptance")).strip().lower()
        in {"yes", "true", "available", "supported"}
        for example in response.data["carrier_examples"]
    )
    if not has_clear_dg_example:
        unknown_fields = {unknown.field for unknown in response.unknowns}
        assert "carrier_capabilities.dangerous_goods_acceptance" in unknown_fields
