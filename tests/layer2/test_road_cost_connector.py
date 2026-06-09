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
from app.services.layer2.connectors.road_cost_connector import fetch_road_cost


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


def _road_request(
    *,
    origin_country: str | None = "IT",
    destination_country: str | None = "FR",
    weight_kg: float | None = 1200,
    volume_cbm: float | None = 12.5,
    dimensions: list[float] | None = None,
    incoterm: str | None = "DAP",
    ready_date: str | None = "2026-06-10",
    deadline: str | None = "2026-06-12",
    cargo_flags: CargoFlags | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-road-cost-connector",
        lane=Lane(
            origin_city="Milan",
            origin_country=origin_country,
            destination_city="Paris",
            destination_country=destination_country,
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
        cargo_flags=cargo_flags or _flags(),
        core_shipment=CoreShipment(
            weight_kg=weight_kg,
            volume_cbm=volume_cbm,
            dimensions=dimensions,
        ),
        commercial=Commercial(
            incoterm=incoterm,
            ready_date=ready_date,
            deadline=deadline,
        ),
    )


def test_road_cost_basic_reference_returns_found_or_unknown():
    response = fetch_road_cost(_road_request())

    assert response.block_id == "ROAD-COST"
    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["cost_status"] == "planning_reference_not_a_quote"
    assert any("not a quote" in factor for factor in response.planning_factors)
    assert isinstance(response.data["cost_reference_examples"], list)


def test_road_cost_missing_lane_adds_unknown():
    response = fetch_road_cost(_road_request(origin_country=None))

    assert "lane.origin_country" in {unknown.field for unknown in response.unknowns}


def test_road_cost_missing_weight_volume_incoterm_adds_unknowns():
    response = fetch_road_cost(
        _road_request(
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


def test_road_cost_does_not_dump_raw_datasets():
    response = fetch_road_cost(_road_request())

    assert not {
        "records",
        "confidence_rules",
        "metadata",
    }.intersection(response.data)
