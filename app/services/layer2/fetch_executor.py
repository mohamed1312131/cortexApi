from __future__ import annotations

from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    FetchPlan,
    ProviderUsed,
    Provenance,
    RequestedMode,
    SourceConfidence,
    Unknown,
    ValidatedShipmentRequest,
)
from app.services.layer2.registry import get_connector


def _unknown_block_response(block_id: str, mode: RequestedMode) -> BlockResponse:
    reason = f"no connector registered for block_id {block_id}"
    return BlockResponse(
        block_id=block_id,
        mode=mode,
        status=BlockStatus.error,
        data={},
        hard_gates=[],
        unknowns=[
            Unknown(
                field="fetch_executor.block_id",
                reason=reason,
                impact="Layer 2 could not fetch this planned block.",
            )
        ],
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.unknown,
            reasons=[reason],
        ),
        provenance=Provenance(
            source="fetch_executor",
            provider_used=ProviderUsed.mock,
            extra={"planned_block_id": block_id},
        ),
    )


def execute_fetch_plan(
    request: ValidatedShipmentRequest,
    plan: FetchPlan,
) -> list[BlockResponse]:
    """Run every planned block.

    A blocking hard gate from one block is recorded on that block (and rolled up
    into the FactPackage / completeness="blocked"), but it no longer suppresses
    the deeper blocks for that mode: the worker still gets carriers, fit,
    documents, security and a cost reference alongside the gate, so the report is
    complete even when a path is blocked. Layer 3 remains the authority on what
    may be claimed about a blocked path.
    """
    responses: list[BlockResponse] = []

    for item in plan.items:
        connector = get_connector(item.block_id)
        if connector is None:
            response = _unknown_block_response(item.block_id, item.mode)
        else:
            response = connector(request)
        responses.append(response)

    return responses
