from __future__ import annotations

import inspect

from app.schemas import (
    BlockResponse,
    BlockStatus,
    CargoFlags,
    Completeness,
    CompletenessStatus,
    Conflict,
    CoreShipment,
    FetchPlan,
    FlagState,
    GateSeverity,
    GateStatus,
    HardGate,
    Lane,
    MissingFields,
    ModeSelection,
    Provenance,
    RequestedMode,
    Unknown,
    UserGoal,
    ValidatedShipmentRequest,
)
from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import ReasoningContext
from app.services.layer2.fact_package_builder import (
    build_fact_package,
    build_rollup,
    compute_completeness,
)
from app.services.layer2.fetch_executor import execute_fetch_plan
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer3 import context_builder
from app.services.layer3.context_builder import (
    _completeness_status,
    _concrete_modes,
    prepare_reasoning_context,
)


# --------------------------------------------------------------------------- #
# factories
# --------------------------------------------------------------------------- #
def _block(
    block_id: str,
    mode: RequestedMode,
    *,
    status: BlockStatus = BlockStatus.found,
    hard_gates: list[HardGate] | None = None,
    unknowns: list[Unknown] | None = None,
    missing_fields: list[str] | None = None,
) -> BlockResponse:
    return BlockResponse(
        block_id=block_id,
        mode=mode,
        status=status,
        hard_gates=hard_gates or [],
        unknowns=unknowns or [],
        missing_fields=missing_fields or [],
        provenance=Provenance(source="test"),
    )


def _manual_package(
    *,
    request: ValidatedShipmentRequest | None = None,
    blocks: list[BlockResponse] | None = None,
    conflicts: list[Conflict] | None = None,
    completeness: Completeness | None = None,
    global_hard_gates: list[HardGate] | None = None,
    global_unknowns: list[Unknown] | None = None,
    global_missing_fields: list[str] | None = None,
) -> FactPackage:
    request = request or ValidatedShipmentRequest(case_id="c1")
    blocks = blocks or []
    rollup = build_rollup(blocks, global_hard_gates, global_unknowns, global_missing_fields)
    completeness = completeness or compute_completeness(rollup, blocks)
    return FactPackage(
        case_id=request.case_id,
        request=request,
        fetch_plan=FetchPlan(case_id=request.case_id),
        block_responses=blocks,
        global_hard_gates=global_hard_gates or [],
        global_unknowns=global_unknowns or [],
        global_missing_fields=global_missing_fields or [],
        conflicts=conflicts or [],
        completeness=completeness,
        derived_rollup=rollup,
    )


def _real_package(origin_country: str, destination_country: str) -> FactPackage:
    request = ValidatedShipmentRequest(
        case_id=f"case-{origin_country}-{destination_country}",
        lane=Lane(origin_country=origin_country, destination_country=destination_country),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
        ),
    )
    plan = build_fetch_plan(request)
    responses = execute_fetch_plan(request, plan)
    return build_fact_package(request, plan, responses)


# --------------------------------------------------------------------------- #
# 1. minimal valid FactPackage
# --------------------------------------------------------------------------- #
def test_returns_reasoning_context_for_minimal_package():
    ctx = prepare_reasoning_context(_manual_package())
    assert isinstance(ctx, ReasoningContext)
    assert ctx.case_id == "c1"


# --------------------------------------------------------------------------- #
# 2. candidate_modes never contains unknown
# --------------------------------------------------------------------------- #
def test_concrete_modes_helper_removes_unknown():
    assert _concrete_modes(
        [RequestedMode.sea, RequestedMode.unknown, RequestedMode.sea]
    ) == [RequestedMode.sea]


def test_candidate_modes_excludes_unknown():
    # default candidate_modes is [sea, air, road]; requested_mode unknown
    ctx = prepare_reasoning_context(_manual_package())
    assert RequestedMode.unknown not in ctx.candidate_modes
    assert ctx.candidate_modes == [RequestedMode.sea, RequestedMode.air, RequestedMode.road]


def test_candidate_modes_falls_back_to_requested_then_covered():
    request = ValidatedShipmentRequest(
        case_id="c1",
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
            needs_mode_selection=False,
        ),
    )
    ctx = prepare_reasoning_context(_manual_package(request=request))
    assert ctx.candidate_modes == [RequestedMode.air]


# --------------------------------------------------------------------------- #
# 3. modes_covered never contains unknown
# --------------------------------------------------------------------------- #
def test_modes_covered_excludes_unknown():
    blocks = [_block("SEA-A", RequestedMode.sea), _block("AIR-A", RequestedMode.air)]
    ctx = prepare_reasoning_context(_manual_package(blocks=blocks))
    assert RequestedMode.unknown not in ctx.modes_covered
    assert set(ctx.modes_covered) == {RequestedMode.sea, RequestedMode.air}


# --------------------------------------------------------------------------- #
# 4. block_statuses includes all block responses
# --------------------------------------------------------------------------- #
def test_block_statuses_include_all_blocks():
    blocks = [
        _block("SEA-A", RequestedMode.sea, status=BlockStatus.found),
        _block("AIR-A", RequestedMode.air, status=BlockStatus.not_found),
        _block("ROAD-A", RequestedMode.road, status=BlockStatus.error),
    ]
    ctx = prepare_reasoning_context(_manual_package(blocks=blocks))
    assert ctx.block_statuses == {
        "SEA-A": "found",
        "AIR-A": "not_found",
        "ROAD-A": "error",
    }


# --------------------------------------------------------------------------- #
# 5. hard gates -> ReasoningFactor with evidence_refs
# --------------------------------------------------------------------------- #
def test_hard_gates_become_reasoning_factors():
    gate = HardGate(
        gate_id="G1",
        mode=RequestedMode.sea,
        severity=GateSeverity.blocking,
        status=GateStatus.triggered,
        message="blocked lane",
        source_block="SEA-A",
        basis="rulebook",
    )
    blocks = [_block("SEA-A", RequestedMode.sea, hard_gates=[gate])]
    ctx = prepare_reasoning_context(_manual_package(blocks=blocks))

    assert len(ctx.hard_gates) == 1
    factor = ctx.hard_gates[0]
    assert factor.code == "G1"
    assert factor.label == "blocked lane"
    assert factor.severity == "blocking"
    assert factor.mode is RequestedMode.sea
    assert factor.evidence_refs == ["gate:SEA-A:G1"]
    # gate trigger-status is decision-critical: exposed as a first-class field...
    assert factor.status == "triggered"
    # ...and still kept in details for audit/human context.
    assert "status=triggered" in factor.details


def test_hard_gate_status_variants_preserved():
    gates = [
        HardGate(
            gate_id="G_TRIG",
            mode=RequestedMode.sea,
            severity=GateSeverity.blocking,
            status=GateStatus.triggered,
            message="m",
            source_block="SEA-A",
        ),
        HardGate(
            gate_id="G_NOT",
            mode=RequestedMode.sea,
            severity=GateSeverity.medium,
            status=GateStatus.not_triggered,
            message="m",
            source_block="SEA-A",
        ),
        HardGate(
            gate_id="G_UNK",
            mode=RequestedMode.sea,
            severity=GateSeverity.low,
            status=GateStatus.unknown,
            message="m",
            source_block="SEA-A",
        ),
    ]
    blocks = [_block("SEA-A", RequestedMode.sea, hard_gates=gates)]
    ctx = prepare_reasoning_context(_manual_package(blocks=blocks))
    by_code = {f.code: f.status for f in ctx.hard_gates}
    assert by_code == {
        "G_TRIG": "triggered",
        "G_NOT": "not_triggered",
        "G_UNK": "unknown",
    }


def test_unknowns_missing_conflicts_have_no_status():
    request = ValidatedShipmentRequest(
        case_id="c1",
        missing_fields=MissingFields(blocking=["weight"]),
    )
    blocks = [
        _block(
            "SEA-A",
            RequestedMode.sea,
            unknowns=[Unknown(field="transit_time", reason="r")],
        )
    ]
    conflicts = [Conflict(type="X", message="m")]
    ctx = prepare_reasoning_context(
        _manual_package(request=request, blocks=blocks, conflicts=conflicts)
    )
    assert all(f.status is None for f in ctx.unknowns)
    assert all(f.status is None for f in ctx.missing_fields)
    assert all(f.status is None for f in ctx.conflicts)


def test_hard_gates_from_real_layer2_package():
    ctx = prepare_reasoning_context(_real_package("CN", "FR"))
    codes = {factor.code for factor in ctx.hard_gates}
    assert "ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL" in codes
    for factor in ctx.hard_gates:
        assert factor.evidence_refs  # every gate carries an evidence ref


# --------------------------------------------------------------------------- #
# 6. unknowns -> ReasoningFactor with evidence_refs
# --------------------------------------------------------------------------- #
def test_unknowns_become_reasoning_factors():
    unknown = Unknown(field="transit_time", reason="no schedule data", impact="cannot estimate ETA")
    blocks = [_block("SEA-A", RequestedMode.sea, unknowns=[unknown])]
    ctx = prepare_reasoning_context(_manual_package(blocks=blocks))

    assert len(ctx.unknowns) == 1
    factor = ctx.unknowns[0]
    assert factor.code == "transit_time"
    assert factor.label == "no schedule data"
    assert factor.mode is RequestedMode.sea
    assert factor.evidence_refs == ["unknown:SEA-A:transit_time"]
    assert factor.details == "cannot estimate ETA"


def test_global_unknown_has_no_mode():
    unknown = Unknown(field="customs", reason="unmapped")
    ctx = prepare_reasoning_context(_manual_package(global_unknowns=[unknown]))
    assert len(ctx.unknowns) == 1
    assert ctx.unknowns[0].mode is None
    assert ctx.unknowns[0].evidence_refs == ["unknown:global:customs"]


# --------------------------------------------------------------------------- #
# 7. missing fields preserved + deduplicated
# --------------------------------------------------------------------------- #
def test_missing_fields_preserved_and_deduplicated():
    request = ValidatedShipmentRequest(
        case_id="c1",
        missing_fields=MissingFields(
            blocking=["weight"],
            high_value=["weight", "incoterm"],
            can_wait=["packaging"],
        ),
    )
    ctx = prepare_reasoning_context(
        _manual_package(request=request, global_missing_fields=["weight"])
    )
    codes = [factor.code for factor in ctx.missing_fields]
    # each field exactly once
    assert codes == ["weight", "incoterm", "packaging"]
    by_code = {factor.code: factor.severity for factor in ctx.missing_fields}
    assert by_code["weight"] == "blocking"          # highest priority wins
    assert by_code["incoterm"] == "high_value"
    assert by_code["packaging"] == "can_wait"


# --------------------------------------------------------------------------- #
# 8. conflicts -> ReasoningFactor
# --------------------------------------------------------------------------- #
def test_conflicts_become_reasoning_factors():
    conflicts = [Conflict(type="MODE_MISMATCH", message="sea vs air", action="ask user")]
    ctx = prepare_reasoning_context(_manual_package(conflicts=conflicts))
    assert len(ctx.conflicts) == 1
    factor = ctx.conflicts[0]
    assert factor.code == "MODE_MISMATCH"
    assert factor.label == "sea vs air"
    assert factor.severity == "conflict"
    assert factor.evidence_refs == ["conflict:0:MODE_MISMATCH"]
    assert factor.details == "ask user"


# --------------------------------------------------------------------------- #
# 9. evidence_refs deterministic + deduplicated
# --------------------------------------------------------------------------- #
def test_evidence_refs_deterministic_and_deduplicated():
    gate = HardGate(
        gate_id="G1",
        mode=RequestedMode.sea,
        severity=GateSeverity.high,
        status=GateStatus.triggered,
        message="m",
        source_block="SEA-A",
    )
    unknown = Unknown(field="transit_time", reason="r")
    blocks = [_block("SEA-A", RequestedMode.sea, hard_gates=[gate], unknowns=[unknown])]
    package = _manual_package(blocks=blocks)

    first = prepare_reasoning_context(package).evidence_refs
    second = prepare_reasoning_context(package).evidence_refs

    ref_ids = [ref.ref_id for ref in first]
    assert len(ref_ids) == len(set(ref_ids))  # no duplicates
    assert ref_ids == [ref.ref_id for ref in second]  # deterministic
    assert "block:SEA-A" in ref_ids
    assert "gate:SEA-A:G1" in ref_ids
    assert "unknown:SEA-A:transit_time" in ref_ids


# --------------------------------------------------------------------------- #
# 10. identical output for identical input
# --------------------------------------------------------------------------- #
def test_identical_input_produces_identical_output():
    package = _real_package("CN", "FR")
    a = prepare_reasoning_context(package).model_dump()
    b = prepare_reasoning_context(package).model_dump()
    assert a == b


# --------------------------------------------------------------------------- #
# 11. no mutation of the FactPackage
# --------------------------------------------------------------------------- #
def test_does_not_mutate_fact_package():
    gate = HardGate(
        gate_id="G1",
        mode=RequestedMode.sea,
        severity=GateSeverity.blocking,
        status=GateStatus.triggered,
        message="m",
        source_block="SEA-A",
    )
    package = _manual_package(blocks=[_block("SEA-A", RequestedMode.sea, hard_gates=[gate])])
    before = package.model_dump()
    prepare_reasoning_context(package)
    assert package.model_dump() == before


# --------------------------------------------------------------------------- #
# 12. request_summary has no user-facing / assistant text
# --------------------------------------------------------------------------- #
def test_request_summary_is_structured_only():
    ctx = prepare_reasoning_context(_manual_package())
    assert "assistant_message" not in ctx.request_summary
    assert "questions_to_user" not in ctx.request_summary
    assert set(ctx.request_summary.keys()) == {
        "cargo_description",
        "weight_kg",
        "volume_cbm",
        "origin_city",
        "origin_country",
        "destination_city",
        "destination_country",
        "requested_mode",
        "candidate_modes",
        "active_profiles",
        "dangerous_goods",
        "priority",
        "ready_for_layer_2",
    }


# --------------------------------------------------------------------------- #
# 13. active_profiles preserved
# --------------------------------------------------------------------------- #
def test_active_profiles_preserved():
    request = ValidatedShipmentRequest(
        case_id="c1",
        active_profiles=["dangerous_goods", "lithium_battery"],
        cargo_flags=CargoFlags(dangerous_goods=FlagState.yes),
        user_goal=UserGoal(),
        core_shipment=CoreShipment(),
    )
    ctx = prepare_reasoning_context(_manual_package(request=request))
    assert ctx.active_profiles == ["dangerous_goods", "lithium_battery"]


# --------------------------------------------------------------------------- #
# 14. completeness_status carried
# --------------------------------------------------------------------------- #
def test_completeness_status_carried():
    package = _manual_package(
        completeness=Completeness(status=CompletenessStatus.blocked, reasons=["x"])
    )
    ctx = prepare_reasoning_context(package)
    assert ctx.completeness_status == "blocked"


def test_completeness_status_helper_handles_none():
    assert _completeness_status(None) is None


# --------------------------------------------------------------------------- #
# 15. no LLM / model import or call in the context builder
# --------------------------------------------------------------------------- #
def test_context_builder_imports_no_llm():
    source = inspect.getsource(context_builder)
    for needle in ("core.llm", "get_chat_model", "ChatOpenAI", "ChatGoogleGenerativeAI", "invoke("):
        assert needle not in source
    assert not hasattr(context_builder, "get_chat_model")
