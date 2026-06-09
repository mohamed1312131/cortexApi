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
from app.services.layer2.connectors.sea_cost_connector import fetch_sea_cost


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
    weight_kg: float | None = 1200,
    volume_cbm: float | None = 12.5,
    dimensions: list[float] | None = None,
    incoterm: str | None = "FOB",
    ready_date: str | None = "2026-06-10",
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-cost-connector",
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
            weight_kg=weight_kg,
            volume_cbm=volume_cbm,
            dimensions=dimensions,
        ),
        commercial=Commercial(
            incoterm=incoterm,
            ready_date=ready_date,
        ),
    )


def test_sea_cost_basic_reference_returns_found_or_unknown():
    response = fetch_sea_cost(_sea_request())

    assert response.block_id == "SEA-COST"
    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["cost_status"] == "planning_reference_not_a_quote"
    assert any("not a quote" in factor for factor in response.planning_factors)


def test_sea_cost_missing_lane_adds_unknown():
    response = fetch_sea_cost(_sea_request(origin_country=None))

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "lane.origin_country" in unknown_fields


def test_sea_cost_missing_weight_volume_incoterm_adds_unknowns():
    response = fetch_sea_cost(
        _sea_request(
            weight_kg=None,
            volume_cbm=None,
            dimensions=None,
            incoterm=None,
        )
    )

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "core_shipment.weight_kg" in unknown_fields
    assert "core_shipment.volume_cbm" in unknown_fields
    assert "commercial.incoterm" in unknown_fields
