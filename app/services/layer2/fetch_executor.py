from __future__ import annotations

from app.schemas import (
    BlockConfidence,
    BlockResponse,
    BlockStatus,
    FetchPlan,
    FetchPriority,
    GateSeverity,
    GateStatus,
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


def _skipped_after_fail_fast_response(
    block_id: str,
    mode: RequestedMode,
    skipped_after: str,
) -> BlockResponse:
    return BlockResponse(
        block_id=block_id,
        mode=mode,
        status=BlockStatus.skipped,
        unknowns=[
            Unknown(
                field="fetch_executor.fail_fast",
                reason=(
                    f"skipped because fail-fast block {skipped_after} triggered "
                    "a blocking hard gate"
                ),
                impact=(
                    "Deeper checks were not executed because the mode is blocked "
                    "at a higher gate."
                ),
            )
        ],
        confidence=BlockConfidence(
            source_confidence=SourceConfidence.unknown,
            reasons=["skipped after fail-fast blocking gate"],
        ),
        provenance=Provenance(
            source="fetch_executor",
            provider_used=ProviderUsed.mock,
            extra={"skipped_after": skipped_after},
        ),
    )


def _has_blocking_triggered_gate(response: BlockResponse) -> bool:
    return any(
        gate.status == GateStatus.triggered
        and gate.severity == GateSeverity.blocking
        for gate in response.hard_gates
    )


def execute_fetch_plan(
    request: ValidatedShipmentRequest,
    plan: FetchPlan,
) -> list[BlockResponse]:
    responses: list[BlockResponse] = []
    skip_mode_after: dict[RequestedMode, str] = {}

    for item in plan.items:
        if item.mode in skip_mode_after:
            responses.append(
                _skipped_after_fail_fast_response(
                    item.block_id,
                    item.mode,
                    skip_mode_after[item.mode],
                )
            )
            continue

        connector = get_connector(item.block_id)
        if connector is None:
            response = _unknown_block_response(item.block_id, item.mode)
        else:
            response = connector(request)

        responses.append(response)

        if (
            item.priority == FetchPriority.fail_fast
            and _has_blocking_triggered_gate(response)
        ):
            skip_mode_after[item.mode] = response.block_id

    return responses
