from __future__ import annotations

from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    FlagState,
    RequestedMode,
    SourceConfidence,
    Unknown,
    ValidatedShipmentRequest,
)
from app.services.layer2.provider_config import provenance_for

BLOCK_ID = "SEA-F"


def fetch_sea_f(request: ValidatedShipmentRequest) -> BlockResponse:
    documents = [
        "commercial_invoice",
        "packing_list",
        "bill_of_lading",
        "verified_gross_mass_vgm",
    ]
    unknowns: list[Unknown] = []
    missing_fields: list[str] = []

    if request.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}:
        documents.extend(
            [
                "dangerous_goods_declaration",
                "safety_data_sheet_sds",
                "emergency_contact_information",
            ]
        )
    elif request.cargo_flags.dangerous_goods == FlagState.unknown:
        unknowns.append(
            Unknown(
                field="cargo_flags.dangerous_goods",
                reason="dangerous goods status is unknown",
                impact=(
                    "SEA-F cannot confirm whether DG maritime documents are required."
                ),
            )
        )

    if not request.commercial.incoterm:
        unknowns.append(
            Unknown(
                field="commercial.incoterm",
                reason="incoterm missing",
                impact=(
                    "Responsibility split for export/import documents cannot be "
                    "confirmed."
                ),
            )
        )

    if request.core_shipment.weight_kg is None:
        missing_fields.append("core_shipment.weight_kg")
        unknowns.append(
            Unknown(
                field="core_shipment.weight_kg",
                reason="shipment weight missing",
                impact="VGM and sea document preparation cannot be fully checked.",
            )
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.sea,
        status=BlockStatus.unknown if unknowns else BlockStatus.found,
        data={
            "documents": documents,
            "document_status": "planning_checklist_only",
            "booking_ready": False,
        },
        planning_factors=[
            (
                "Maritime document checklist is planning-only until "
                "carrier/forwarder confirms booking requirements."
            ),
            (
                "Verified Gross Mass (VGM) is required before vessel loading "
                "where applicable."
            ),
        ],
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.planning_reference,
        ),
        provenance=provenance_for(BLOCK_ID, "sea_document_planning_reference"),
    )
