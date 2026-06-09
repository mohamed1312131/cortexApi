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
from app.services.layer2.connectors.road_f_connector import fetch_road_f


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
    cargo_flags: CargoFlags | None = None,
    incoterm: str | None = "DAP",
    ready_date: str | None = "2026-06-10",
    deadline: str | None = "2026-06-12",
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-road-f-connector",
        lane=Lane(
            origin_city="Milan",
            origin_country="IT",
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
        cargo_flags=cargo_flags or _flags(),
        core_shipment=CoreShipment(
            cargo_description="industrial spare parts",
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(
            incoterm=incoterm,
            ready_date=ready_date,
            deadline=deadline,
        ),
    )


def test_road_f_basic_documents_driver_hours_returns_found_or_unknown():
    response = fetch_road_f(_road_request())

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert response.data["road_preparation_status"] == (
        "planning_only_requires_carrier_border_validation"
    )
    assert isinstance(response.data["matched_document_requirements"], list)
    assert isinstance(response.data["matched_driver_hours_rules"], list)
    assert isinstance(response.data["document_requirement_count"], int)
    assert "cmr_waybill" in response.data["documents"]


def test_road_f_missing_incoterm_ready_deadline_adds_unknowns():
    response = fetch_road_f(
        _road_request(incoterm=None, ready_date=None, deadline=None)
    )

    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert "commercial.incoterm" in unknown_fields
    assert "commercial.ready_date" in unknown_fields
    assert "commercial.deadline" in unknown_fields
    assert response.confidence.cap == 0.5


def test_road_f_dg_includes_adr_or_unknown():
    response = fetch_road_f(
        _road_request(cargo_flags=_flags(dangerous_goods=FlagState.yes))
    )

    matched_text = " ".join(
        str(record) for record in response.data["matched_document_requirements"]
    ).lower()
    unknown_fields = {unknown.field for unknown in response.unknowns}
    assert (
        "adr" in matched_text
        or "dangerous goods" in matched_text
        or "road_f.dg_documents" in unknown_fields
    )


def test_road_f_oversized_adds_unknown_and_planning_factor():
    response = fetch_road_f(
        _road_request(cargo_flags=_flags(oversized=FlagState.yes))
    )

    assert "cargo_flags.oversized" in {
        unknown.field for unknown in response.unknowns
    }
    assert any("Oversized road movement" in factor for factor in response.planning_factors)


def test_road_f_does_not_dump_raw_datasets():
    response = fetch_road_f(_road_request())

    assert not {
        "records",
        "document_requirements",
        "driver_hours_rules",
        "border_buffer_reference",
        "confidence_rules",
    }.intersection(response.data)
