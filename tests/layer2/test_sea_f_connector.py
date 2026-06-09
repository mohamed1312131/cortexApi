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
from app.services.layer2.connectors.sea_f_connector import fetch_sea_f


def _sea_request(
    *,
    dangerous_goods: FlagState = FlagState.no,
    weight_kg: float | None = 1200,
    incoterm: str | None = "FOB",
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-sea-f-connector",
        lane=Lane(origin_city="Shanghai", origin_country="CN"),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        core_shipment=CoreShipment(weight_kg=weight_kg),
        commercial=Commercial(incoterm=incoterm),
    )


def test_sea_f_base_documents_found():
    response = fetch_sea_f(_sea_request())

    assert response.status == BlockStatus.found
    documents = response.data["documents"]
    assert "commercial_invoice" in documents
    assert "packing_list" in documents
    assert "bill_of_lading" in documents
    assert "verified_gross_mass_vgm" in documents
    assert response.data["booking_ready"] is False


def test_sea_f_includes_dg_documents_when_dg_yes():
    response = fetch_sea_f(_sea_request(dangerous_goods=FlagState.yes))

    documents = response.data["documents"]
    assert "dangerous_goods_declaration" in documents
    assert "safety_data_sheet_sds" in documents


def test_sea_f_unknown_dg_adds_unknown():
    response = fetch_sea_f(_sea_request(dangerous_goods=FlagState.unknown))

    assert response.status == BlockStatus.unknown
    assert any(
        unknown.field == "cargo_flags.dangerous_goods"
        for unknown in response.unknowns
    )


def test_sea_f_missing_weight_adds_unknown_and_missing_field():
    response = fetch_sea_f(_sea_request(weight_kg=None))

    assert response.status == BlockStatus.unknown
    assert "core_shipment.weight_kg" in response.missing_fields
