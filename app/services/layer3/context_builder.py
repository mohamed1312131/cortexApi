# app/services/layer3/context_builder.py
from __future__ import annotations

from app.schemas.block_response import BlockResponse, HardGate, Unknown
from app.schemas.fact_package import Completeness, Conflict, FactPackage
from app.schemas.layer3 import EvidenceRef, ReasoningContext, ReasoningFactor
from app.schemas.shipment_request import RequestedMode, ValidatedShipmentRequest

# prepare_reasoning_context: deterministic FactPackage -> ReasoningContext.
#
# This is a PURE read-model projection. No LLM, no randomness, no I/O, no scoring,
# no ranking. The same FactPackage must always yield the identical ReasoningContext.
# Nothing here mutates the FactPackage or the ValidatedShipmentRequest.
#
# Source-of-truth note: in the current Layer 2, global_hard_gates / global_unknowns
# / global_missing_fields are empty and the populated facts live at the block level
# (and in derived_rollup). We aggregate from block_responses (so each gate/unknown
# keeps its owning block + mode) AND defensively merge the global_* lists, deduped,
# so this stays correct if Layer 2 starts promoting facts to the global level.

_GLOBAL = "global"

# severity tokens (string, since ReasoningFactor.severity is a free str)
_SEV_UNKNOWN = "unknown"
_SEV_CONFLICT = "conflict"

# missing-field severity classes, highest priority first (used for dedup ordering)
_MISSING_BLOCKING = "blocking"
_MISSING_HIGH_VALUE = "high_value"
_MISSING_CAN_WAIT = "can_wait"
_MISSING_GLOBAL = "global"
_MISSING_BLOCK = "block"
_MISSING_PRIORITY = (
    _MISSING_BLOCKING,
    _MISSING_HIGH_VALUE,
    _MISSING_CAN_WAIT,
    _MISSING_GLOBAL,
    _MISSING_BLOCK,
)


def prepare_reasoning_context(fact_package: FactPackage) -> ReasoningContext:
    """Project a Layer 2 FactPackage into the compact Layer 3 ReasoningContext.

    Deterministic and side-effect free. Preserves hard gates, unknowns, missing
    fields, conflicts, confidence-cap reasons, block statuses, modes covered,
    active profiles and evidence refs. Never invents facts and never treats an
    unknown as safe.
    """
    request = fact_package.request
    blocks = list(fact_package.block_responses)

    modes_covered = _concrete_modes(fact_package.derived_rollup.modes_covered)
    candidate_modes = _candidate_modes(request, modes_covered, blocks)

    return ReasoningContext(
        case_id=_case_id(fact_package),
        request_summary=_request_summary(request),
        candidate_modes=candidate_modes,
        active_profiles=list(request.active_profiles),
        modes_covered=modes_covered,
        block_statuses=_block_statuses(blocks),
        hard_gates=_hard_gate_factors(blocks, fact_package.global_hard_gates),
        unknowns=_unknown_factors(blocks, fact_package.global_unknowns),
        missing_fields=_missing_field_factors(fact_package),
        conflicts=_conflict_factors(fact_package.conflicts),
        confidence_cap_reasons=_confidence_cap_reasons(fact_package),
        evidence_refs=_evidence_refs(fact_package, blocks),
        completeness_status=_completeness_status(fact_package.completeness),
    )


# --------------------------------------------------------------------------- #
# scalars
# --------------------------------------------------------------------------- #
def _case_id(fact_package: FactPackage) -> str:
    return fact_package.request.case_id or fact_package.case_id


def _completeness_status(completeness: Completeness | None) -> str | None:
    if completeness is None:
        return None
    return completeness.status.value


def _request_summary(request: ValidatedShipmentRequest) -> dict:
    """Stable, structured-only summary. No assistant_message / user-facing text."""
    return {
        "cargo_description": request.core_shipment.cargo_description,
        "weight_kg": request.core_shipment.weight_kg,
        "volume_cbm": request.core_shipment.volume_cbm,
        "origin_city": request.lane.origin_city,
        "origin_country": request.lane.origin_country,
        "destination_city": request.lane.destination_city,
        "destination_country": request.lane.destination_country,
        "requested_mode": request.mode.requested_mode.value,
        "candidate_modes": [m.value for m in request.mode.candidate_modes],
        "active_profiles": list(request.active_profiles),
        "dangerous_goods": request.cargo_flags.dangerous_goods.value,
        "priority": request.user_goal.priority.value,
        "ready_for_layer_2": request.ready_for_layer_2,
    }


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #
def _concrete_modes(modes: list[RequestedMode]) -> list[RequestedMode]:
    result: list[RequestedMode] = []
    for mode in modes:
        if mode is not RequestedMode.unknown and mode not in result:
            result.append(mode)
    return result


def _candidate_modes(
    request: ValidatedShipmentRequest,
    modes_covered: list[RequestedMode],
    blocks: list[BlockResponse],
) -> list[RequestedMode]:
    candidates = _concrete_modes(request.mode.candidate_modes)
    if candidates:
        return candidates

    if request.mode.requested_mode is not RequestedMode.unknown:
        return [request.mode.requested_mode]

    if modes_covered:
        return list(modes_covered)

    # last resort: concrete modes actually present on block responses (no invention)
    return _concrete_modes([block.mode for block in blocks])


# --------------------------------------------------------------------------- #
# block statuses
# --------------------------------------------------------------------------- #
def _block_statuses(blocks: list[BlockResponse]) -> dict[str, str]:
    return {block.block_id: block.status.value for block in blocks}


# --------------------------------------------------------------------------- #
# hard gates
# --------------------------------------------------------------------------- #
def _gate_ref_id(gate: HardGate) -> str:
    return f"gate:{gate.source_block}:{gate.gate_id}"


def _hard_gate_factors(
    blocks: list[BlockResponse],
    global_hard_gates: list[HardGate],
) -> list[ReasoningFactor]:
    factors: list[ReasoningFactor] = []
    seen: set[str] = set()
    for gate in [g for block in blocks for g in block.hard_gates] + list(global_hard_gates):
        ref = _gate_ref_id(gate)
        if ref in seen:
            continue
        seen.add(ref)
        factors.append(
            ReasoningFactor(
                code=gate.gate_id,
                label=gate.message,
                severity=gate.severity.value,
                mode=gate.mode,
                evidence_refs=[ref],
                # status is decision-critical; expose it as a first-class field so
                # the deterministic engine never parses `details`. details is kept
                # for human/audit context only.
                status=gate.status.value,
                details=_gate_details(gate),
            )
        )
    return factors


def _gate_details(gate: HardGate) -> str:
    parts = [f"status={gate.status.value}", f"source_block={gate.source_block}"]
    if gate.basis:
        parts.append(f"basis={gate.basis}")
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
# unknowns
# --------------------------------------------------------------------------- #
def _unknown_ref_id(source_block: str, field: str) -> str:
    return f"unknown:{source_block}:{field}"


def _unknown_factors(
    blocks: list[BlockResponse],
    global_unknowns: list[Unknown],
) -> list[ReasoningFactor]:
    factors: list[ReasoningFactor] = []
    seen: set[str] = set()

    # block-level unknowns keep their owning block + mode; Unknown has no mode/source
    pairs: list[tuple[str, RequestedMode | None, Unknown]] = []
    for block in blocks:
        for unknown in block.unknowns:
            pairs.append((block.block_id, block.mode, unknown))
    for unknown in global_unknowns:
        pairs.append((_GLOBAL, None, unknown))

    for source_block, mode, unknown in pairs:
        ref = _unknown_ref_id(source_block, unknown.field)
        if ref in seen:
            continue
        seen.add(ref)
        factors.append(
            ReasoningFactor(
                code=unknown.field,
                label=unknown.reason,
                severity=_SEV_UNKNOWN,
                mode=mode,
                evidence_refs=[ref],
                details=unknown.impact,
            )
        )
    return factors


# --------------------------------------------------------------------------- #
# missing fields
# --------------------------------------------------------------------------- #
def _missing_field_factors(fact_package: FactPackage) -> list[ReasoningFactor]:
    request = fact_package.request
    # (field_label, severity_class) in priority order; first occurrence wins.
    candidates: list[tuple[str, str]] = []
    for field in request.missing_fields.blocking:
        candidates.append((field, _MISSING_BLOCKING))
    for field in request.missing_fields.high_value:
        candidates.append((field, _MISSING_HIGH_VALUE))
    for field in request.missing_fields.can_wait:
        candidates.append((field, _MISSING_CAN_WAIT))
    for field in fact_package.global_missing_fields:
        candidates.append((field, _MISSING_GLOBAL))
    for field in fact_package.derived_rollup.missing_fields:
        candidates.append((field, _MISSING_BLOCK))

    best: dict[str, str] = {}
    order: list[str] = []
    for field, severity in candidates:
        if field not in best:
            best[field] = severity
            order.append(field)
        elif _MISSING_PRIORITY.index(severity) < _MISSING_PRIORITY.index(best[field]):
            best[field] = severity

    return [
        ReasoningFactor(
            code=field,
            label=field,
            severity=best[field],
            evidence_refs=[f"missing:{field}"],
        )
        for field in order
    ]


# --------------------------------------------------------------------------- #
# conflicts
# --------------------------------------------------------------------------- #
def _conflict_factors(conflicts: list[Conflict]) -> list[ReasoningFactor]:
    factors: list[ReasoningFactor] = []
    for index, conflict in enumerate(conflicts):
        factors.append(
            ReasoningFactor(
                code=conflict.type,
                label=conflict.message,
                severity=_SEV_CONFLICT,
                evidence_refs=[f"conflict:{index}:{conflict.type}"],
                details=conflict.action,
            )
        )
    return factors


# --------------------------------------------------------------------------- #
# confidence cap reasons
# --------------------------------------------------------------------------- #
def _confidence_cap_reasons(fact_package: FactPackage) -> list[str]:
    reasons: list[str] = []
    seen: set[str] = set()

    def _add(reason: str) -> None:
        if reason not in seen:
            seen.add(reason)
            reasons.append(reason)

    explicit_found = False
    for cap in fact_package.derived_rollup.confidence_caps:
        for reason in cap.reasons:
            explicit_found = True
            _add(f"{cap.source_block}: {reason}" if cap.source_block else reason)

    # Only derive transparent reasons when no explicit cap reasons exist. No scores.
    if not explicit_found:
        completeness = fact_package.completeness
        if completeness is not None and completeness.status.value != "complete_enough":
            _add(f"completeness:{completeness.status.value}")
        if fact_package.derived_rollup.hard_gates:
            _add("hard gates present")
        if fact_package.derived_rollup.unknowns:
            _add("unknowns present")

    return reasons


# --------------------------------------------------------------------------- #
# evidence refs
# --------------------------------------------------------------------------- #
def _evidence_refs(
    fact_package: FactPackage,
    blocks: list[BlockResponse],
) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    seen: set[str] = set()

    def _add(ref: EvidenceRef) -> None:
        if ref.ref_id not in seen:
            seen.add(ref.ref_id)
            refs.append(ref)

    # one ref per block response
    for block in blocks:
        _add(
            EvidenceRef(
                ref_id=f"block:{block.block_id}",
                source_type="block",
                source_block=block.block_id,
                mode=block.mode,
            )
        )

    # hard gate sources (block-level + global)
    for gate in [g for block in blocks for g in block.hard_gates] + list(
        fact_package.global_hard_gates
    ):
        _add(
            EvidenceRef(
                ref_id=_gate_ref_id(gate),
                source_type="hard_gate",
                source_block=gate.source_block,
                mode=gate.mode,
                basis=gate.basis,
            )
        )

    # unknown sources (block-level keep mode + block; global has neither)
    for block in blocks:
        for unknown in block.unknowns:
            _add(
                EvidenceRef(
                    ref_id=_unknown_ref_id(block.block_id, unknown.field),
                    source_type="unknown",
                    source_block=block.block_id,
                    mode=block.mode,
                    field_path=unknown.field,
                )
            )
    for unknown in fact_package.global_unknowns:
        _add(
            EvidenceRef(
                ref_id=_unknown_ref_id(_GLOBAL, unknown.field),
                source_type="unknown",
                source_block=_GLOBAL,
                field_path=unknown.field,
            )
        )

    # conflict sources
    for index, conflict in enumerate(fact_package.conflicts):
        _add(
            EvidenceRef(
                ref_id=f"conflict:{index}:{conflict.type}",
                source_type="conflict",
            )
        )

    # missing-field sources (request + global + block-derived)
    missing_fields: list[str] = []
    for field in (
        list(fact_package.request.missing_fields.blocking)
        + list(fact_package.request.missing_fields.high_value)
        + list(fact_package.request.missing_fields.can_wait)
        + list(fact_package.global_missing_fields)
        + list(fact_package.derived_rollup.missing_fields)
    ):
        if field not in missing_fields:
            missing_fields.append(field)
    for field in missing_fields:
        _add(
            EvidenceRef(
                ref_id=f"missing:{field}",
                source_type="missing_field",
                field_path=field,
            )
        )

    return refs
