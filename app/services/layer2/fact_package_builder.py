from __future__ import annotations

from app.schemas import (
    BlockResponse,
    BlockStatus,
    Completeness,
    CompletenessStatus,
    ConfidenceCap,
    FactPackage,
    FactPackageRollup,
    FetchPlan,
    GateSeverity,
    GateStatus,
    HardGate,
    RequestedMode,
    Unknown,
    ValidatedShipmentRequest,
)
from app.services.layer2.conflict_detector import detect_conflicts


def build_rollup(
    block_responses: list[BlockResponse],
    global_hard_gates: list[HardGate] | None = None,
    global_unknowns: list[Unknown] | None = None,
    global_missing_fields: list[str] | None = None,
) -> FactPackageRollup:
    hard_gates = [
        gate
        for response in block_responses
        for gate in response.hard_gates
    ]
    hard_gates.extend(global_hard_gates or [])

    unknowns = [
        unknown
        for response in block_responses
        for unknown in response.unknowns
    ]
    unknowns.extend(global_unknowns or [])

    missing_fields: list[str] = []
    for field in [
        field
        for response in block_responses
        for field in response.missing_fields
    ] + list(global_missing_fields or []):
        if field not in missing_fields:
            missing_fields.append(field)

    confidence_caps = [
        ConfidenceCap(
            cap=response.confidence.cap,
            reasons=list(response.confidence.reasons),
            source_block=response.block_id,
        )
        for response in block_responses
        if response.confidence.cap is not None
    ]

    modes_covered: list[RequestedMode] = []
    for response in block_responses:
        if response.mode != RequestedMode.unknown and response.mode not in modes_covered:
            modes_covered.append(response.mode)

    blocks_called = [response.block_id for response in block_responses]
    blocks_failed = [
        response.block_id
        for response in block_responses
        if response.status == BlockStatus.error
    ]
    blocks_empty = [
        response.block_id
        for response in block_responses
        if response.status in {BlockStatus.not_found, BlockStatus.unknown}
    ]

    return FactPackageRollup(
        hard_gates=hard_gates,
        unknowns=unknowns,
        missing_fields=missing_fields,
        confidence_caps=confidence_caps,
        modes_covered=modes_covered,
        blocks_called=blocks_called,
        blocks_failed=blocks_failed,
        blocks_empty=blocks_empty,
    )


def compute_completeness(
    rollup: FactPackageRollup,
    block_responses: list[BlockResponse],
) -> Completeness:
    for gate in rollup.hard_gates:
        if (
            gate.status == GateStatus.triggered
            and gate.severity == GateSeverity.blocking
        ):
            return Completeness(
                status=CompletenessStatus.blocked,
                reasons=["blocking hard gate triggered"],
            )

    if any(response.status == BlockStatus.error for response in block_responses):
        return Completeness(
            status=CompletenessStatus.insufficient,
            reasons=["one or more planned blocks failed"],
        )

    if rollup.unknowns or rollup.missing_fields:
        return Completeness(
            status=CompletenessStatus.incomplete_but_usable,
            reasons=["unknowns or missing fields present"],
        )

    return Completeness(status=CompletenessStatus.complete_enough, reasons=[])


def build_fact_package(
    request: ValidatedShipmentRequest,
    fetch_plan: FetchPlan,
    block_responses: list[BlockResponse],
) -> FactPackage:
    global_hard_gates: list[HardGate] = []
    global_unknowns: list[Unknown] = []
    global_missing_fields: list[str] = []
    derived_rollup = build_rollup(
        block_responses=block_responses,
        global_hard_gates=global_hard_gates,
        global_unknowns=global_unknowns,
        global_missing_fields=global_missing_fields,
    )
    conflicts = detect_conflicts(block_responses)

    return FactPackage(
        case_id=request.case_id,
        request=request,
        fetch_plan=fetch_plan,
        block_responses=block_responses,
        global_hard_gates=global_hard_gates,
        global_unknowns=global_unknowns,
        global_missing_fields=global_missing_fields,
        conflicts=conflicts,
        completeness=compute_completeness(derived_rollup, block_responses),
        derived_rollup=derived_rollup,
    )
