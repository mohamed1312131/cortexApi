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
from app.services.layer2.connectors.road_a_connector import fetch_road_a
from app.services.layer2.connectors.road_b_connector import fetch_road_b
from app.services.layer2.connectors.road_f_connector import fetch_road_f


def _request(
    *,
    dangerous_goods: FlagState = FlagState.unknown,
    profiles=None,
    weight_kg: float | None = None,
    dimensions: list[float] | None = None,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-road-extended-connectors",
        lane=Lane(origin_country="IT", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        core_shipment=CoreShipment(
            weight_kg=weight_kg,
            dimensions=dimensions,
        ),
        profiles=profiles or {},
    )


def test_road_a_dg_unknown_returns_unknown():
    response = fetch_road_a(_request(dangerous_goods=FlagState.unknown))

    assert response.status == BlockStatus.unknown
    assert response.unknowns
    assert response.unknowns[0].field == "cargo_flags.dangerous_goods"


def test_road_a_dg_yes_without_un_number_returns_unknown():
    response = fetch_road_a(_request(dangerous_goods=FlagState.yes))

    assert response.status == BlockStatus.unknown
    assert "profiles.dangerous_goods.un_number" in response.missing_fields


def test_road_a_dg_likely_with_un_number_returns_found_and_planning_factor():
    response = fetch_road_a(
        _request(
            dangerous_goods=FlagState.likely,
            profiles={"dangerous_goods": {"un_number": "UN3480"}},
        )
    )

    assert response.status == BlockStatus.found
    assert response.data["un_number"] == "UN3480"
    assert response.planning_factors


def test_road_b_missing_dimensions_returns_unknown():
    response = fetch_road_b(_request(weight_kg=1200))

    assert response.status == BlockStatus.unknown
    assert "core_shipment.dimensions" in response.missing_fields


def test_road_f_returns_found_and_includes_cmr_waybill():
    response = fetch_road_f(_request())

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert "cmr_waybill" in response.data["documents"]


def test_road_f_includes_adr_document_when_dg_yes_or_likely():
    response = fetch_road_f(_request(dangerous_goods=FlagState.yes))

    assert response.status in {BlockStatus.found, BlockStatus.unknown}
    assert "ADR transport document / DG declaration" in response.data["documents"]
