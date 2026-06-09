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

BLOCK_ID = "ROAD-A"


def fetch_road_a(request: ValidatedShipmentRequest) -> BlockResponse:
    provenance = provenance_for(BLOCK_ID, "request_profiles")
    dangerous_goods = request.cargo_flags.dangerous_goods

    if dangerous_goods == FlagState.no:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.not_applicable,
            data={"dangerous_goods": "no"},
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.authored,
            ),
            provenance=provenance,
        )

    if dangerous_goods == FlagState.unknown:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            unknowns=[
                Unknown(
                    field="cargo_flags.dangerous_goods",
                    reason="dangerous goods status is unknown",
                    impact="Road ADR requirements cannot be confirmed.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    profile = request.profiles.get("dangerous_goods", {})
    if not isinstance(profile, dict):
        profile = {}
    un_number = profile.get("un_number")

    if not un_number:
        return BlockResponse(
            block_id=BLOCK_ID,
            mode=RequestedMode.road,
            status=BlockStatus.unknown,
            data={"dangerous_goods": "yes_or_likely"},
            missing_fields=["profiles.dangerous_goods.un_number"],
            unknowns=[
                Unknown(
                    field="profiles.dangerous_goods.un_number",
                    reason="UN number missing for dangerous goods cargo",
                    impact="ADR classification and acceptance cannot be confirmed.",
                )
            ],
            confidence=BlockConfidence(
                source_confidence=SourceConfidence.unknown,
            ),
            provenance=provenance,
        )

    return BlockResponse(
        block_id=BLOCK_ID,
        mode=RequestedMode.road,
        status=BlockStatus.found,
        data={
            "dangerous_goods": "yes_or_likely",
            "un_number": un_number,
            "adr_check": "requires_specialist_validation",
        },
        planning_factors=[
            "ADR requirements must be validated by carrier/specialist before booking."
        ],
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.authored,
        ),
        provenance=provenance,
    )
