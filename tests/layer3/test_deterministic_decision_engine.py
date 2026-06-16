from __future__ import annotations

from app.schemas.layer3 import EvidenceRef, ReasoningContext, ReasoningFactor
from app.schemas.reasoning_decision import ConfidenceBand, RankingType, ReadinessBand
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.deterministic_decision_engine import (
    build_deterministic_decision,
)

HIGH = ReadinessBand.HIGH
MEDIUM = ReadinessBand.MEDIUM
MEDIUM_LOW = ReadinessBand.MEDIUM_LOW
LOW = ReadinessBand.LOW
SPECIALIZED = ReadinessBand.SPECIALIZED_STUDY_REQUIRED
BLOCKED = ReadinessBand.BLOCKED

_ORDER = {
    BLOCKED: 0,
    SPECIALIZED: 1,
    LOW: 2,
    MEDIUM_LOW: 3,
    MEDIUM: 4,
    HIGH: 5,
}


# --------------------------------------------------------------------------- #
# local ReasoningContext factories (no FactPackage builder here)
# --------------------------------------------------------------------------- #
def _block_ref(mode: RequestedMode) -> EvidenceRef:
    block_id = f"{mode.value.upper()}-A"
    return EvidenceRef(ref_id=f"block:{block_id}", source_type="block", source_block=block_id, mode=mode)


def _ctx(
    *,
    case_id: str = "c1",
    candidate_modes: list[RequestedMode] | None = None,
    modes_covered: list[RequestedMode] | None = None,
    active_profiles: list[str] | None = None,
    hard_gates: list[ReasoningFactor] | None = None,
    unknowns: list[ReasoningFactor] | None = None,
    missing_fields: list[ReasoningFactor] | None = None,
    conflicts: list[ReasoningFactor] | None = None,
    completeness_status: str | None = "complete_enough",
    evidence_refs: list[EvidenceRef] | None = None,
) -> ReasoningContext:
    candidate_modes = [RequestedMode.road] if candidate_modes is None else candidate_modes
    evidence_modes = candidate_modes or (modes_covered or [])
    if evidence_refs is None:
        evidence_refs = [_block_ref(m) for m in evidence_modes]
    return ReasoningContext(
        case_id=case_id,
        candidate_modes=candidate_modes,
        modes_covered=modes_covered or [],
        active_profiles=active_profiles or [],
        block_statuses={},
        hard_gates=hard_gates or [],
        unknowns=unknowns or [],
        missing_fields=missing_fields or [],
        conflicts=conflicts or [],
        confidence_cap_reasons=[],
        evidence_refs=evidence_refs,
        completeness_status=completeness_status,
    )


def _gate(code: str, mode: RequestedMode, severity: str, status: str) -> ReasoningFactor:
    src = f"{mode.value.upper()}-A"
    return ReasoningFactor(
        code=code,
        label=code,
        severity=severity,
        mode=mode,
        evidence_refs=[f"gate:{src}:{code}"],
        status=status,
    )


def _unknown(code: str, *, mode: RequestedMode | None = None, severity: str = "unknown") -> ReasoningFactor:
    src = f"{mode.value.upper()}-A" if mode else "global"
    return ReasoningFactor(
        code=code,
        label=code,
        severity=severity,
        mode=mode,
        evidence_refs=[f"unknown:{src}:{code}"],
    )


def _missing(code: str, severity: str) -> ReasoningFactor:
    return ReasoningFactor(code=code, label=code, severity=severity, evidence_refs=[f"missing:{code}"])


def _conflict(code: str, *, mode: RequestedMode | None = None) -> ReasoningFactor:
    return ReasoningFactor(
        code=code,
        label=code,
        severity="conflict",
        mode=mode,
        evidence_refs=[f"conflict:0:{code}"],
    )


def _band_of(decision, mode: RequestedMode) -> ReadinessBand:
    for path in decision.ranked_path_families:
        if path.mode is mode:
            return path.readiness_band
    raise AssertionError(f"no ranked path for {mode}")


def _all_keys(obj) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(key)
            keys |= _all_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


# --------------------------------------------------------------------------- #
# 1 + 2. determinism
# --------------------------------------------------------------------------- #
def test_decision_is_deterministic():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        hard_gates=[_gate("G1", RequestedMode.road, "high", "triggered")],
        unknowns=[_unknown("transit", severity="high")],
        missing_fields=[_missing("incoterm", "high_value")],
        completeness_status="incomplete_but_usable",
    )
    d1, _ = build_deterministic_decision(ctx, trace_id="t1")
    d2, _ = build_deterministic_decision(ctx, trace_id="t1")
    assert d1.model_dump() == d2.model_dump()


def test_trace_is_deterministic_for_same_trace_id():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        unknowns=[_unknown("transit")],
    )
    _, t1 = build_deterministic_decision(ctx, trace_id="t1")
    _, t2 = build_deterministic_decision(ctx, trace_id="t1")
    assert t1.model_dump() == t2.model_dump()


# --------------------------------------------------------------------------- #
# 3 + 4. hard gate trigger semantics
# --------------------------------------------------------------------------- #
def test_triggered_blocking_gate_blocks_mode():
    ctx = _ctx(hard_gates=[_gate("BLK", RequestedMode.road, "blocking", "triggered")])
    decision, _ = build_deterministic_decision(ctx)
    assert _band_of(decision, RequestedMode.road) is BLOCKED


def test_not_triggered_blocking_gate_does_not_block():
    ctx = _ctx(hard_gates=[_gate("BLK", RequestedMode.road, "blocking", "not_triggered")])
    decision, _ = build_deterministic_decision(ctx)
    assert _band_of(decision, RequestedMode.road) is HIGH


# --------------------------------------------------------------------------- #
# 5. unknown-status high gate caps below HIGH
# --------------------------------------------------------------------------- #
def test_unknown_status_high_gate_caps_below_high():
    ctx = _ctx(hard_gates=[_gate("G", RequestedMode.road, "high", "unknown")])
    decision, _ = build_deterministic_decision(ctx)
    band = _band_of(decision, RequestedMode.road)
    assert band is SPECIALIZED
    assert _ORDER[band] < _ORDER[HIGH]


# --------------------------------------------------------------------------- #
# 6. high unknown caps below HIGH
# --------------------------------------------------------------------------- #
def test_high_unknown_caps_below_high():
    ctx = _ctx(unknowns=[_unknown("classification", severity="high")])
    decision, _ = build_deterministic_decision(ctx)
    band = _band_of(decision, RequestedMode.road)
    assert band is MEDIUM_LOW
    assert _ORDER[band] < _ORDER[HIGH]


# --------------------------------------------------------------------------- #
# 7 + 8. missing fields
# --------------------------------------------------------------------------- #
def test_blocking_missing_field_specializes():
    ctx = _ctx(missing_fields=[_missing("weight", "blocking")])
    decision, _ = build_deterministic_decision(ctx)
    assert _band_of(decision, RequestedMode.road) in {BLOCKED, SPECIALIZED}


def test_high_value_missing_field_caps_but_not_block():
    ctx = _ctx(missing_fields=[_missing("incoterm", "high_value")])
    decision, _ = build_deterministic_decision(ctx)
    band = _band_of(decision, RequestedMode.road)
    assert band is MEDIUM
    assert band is not BLOCKED


# --------------------------------------------------------------------------- #
# 9 + 10. overall band
# --------------------------------------------------------------------------- #
def test_all_blocked_paths_make_overall_blocked():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        hard_gates=[
            _gate("R", RequestedMode.road, "blocking", "triggered"),
            _gate("S", RequestedMode.sea, "blocking", "triggered"),
        ],
    )
    decision, _ = build_deterministic_decision(ctx)
    assert decision.overall_readiness_band is BLOCKED
    assert all(p.readiness_band is BLOCKED for p in decision.ranked_path_families)
    assert decision.ranking_type is RankingType.blocked_ranking


def test_best_viable_path_determines_overall_band():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        hard_gates=[_gate("R", RequestedMode.road, "blocking", "triggered")],
        missing_fields=[_missing("incoterm", "high_value")],  # caps everything to MEDIUM
    )
    decision, _ = build_deterministic_decision(ctx)
    assert decision.overall_readiness_band is MEDIUM
    assert decision.ranked_path_families[0].mode is RequestedMode.sea


# --------------------------------------------------------------------------- #
# 11. stable tie-break (road, sea, air)
# --------------------------------------------------------------------------- #
def test_ranking_tiebreak_is_stable():
    ctx = _ctx(candidate_modes=[RequestedMode.air, RequestedMode.sea, RequestedMode.road])
    decision, _ = build_deterministic_decision(ctx)
    assert [p.mode for p in decision.ranked_path_families] == [
        RequestedMode.road,
        RequestedMode.sea,
        RequestedMode.air,
    ]
    assert [p.rank for p in decision.ranked_path_families] == [1, 2, 3]


# --------------------------------------------------------------------------- #
# 12 + 13. output hygiene
# --------------------------------------------------------------------------- #
def test_no_unknown_mode_in_ranked_output():
    ctx = _ctx(candidate_modes=[RequestedMode.road, RequestedMode.sea, RequestedMode.air])
    decision, _ = build_deterministic_decision(ctx)
    assert all(p.mode is not RequestedMode.unknown for p in decision.ranked_path_families)


def test_every_ranked_path_has_evidence_refs():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        unknowns=[_unknown("transit")],
        missing_fields=[_missing("incoterm", "high_value")],
    )
    decision, _ = build_deterministic_decision(ctx)
    assert decision.ranked_path_families
    for path in decision.ranked_path_families:
        assert path.evidence_refs


def test_skips_mode_with_no_evidence():
    # candidate sea but no evidence ref for sea -> no path, not invented
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        evidence_refs=[_block_ref(RequestedMode.road)],
    )
    decision, trace = build_deterministic_decision(ctx)
    assert {p.mode for p in decision.ranked_path_families} == {RequestedMode.road}
    assert any("sea_preparation" in note for note in trace.notes)


# --------------------------------------------------------------------------- #
# 14 + 15. raw scores only in the trace
# --------------------------------------------------------------------------- #
def test_decision_has_no_raw_score_keys():
    ctx = _ctx(unknowns=[_unknown("transit", severity="high")])
    decision, _ = build_deterministic_decision(ctx)
    keys = _all_keys(decision.model_dump())
    assert "raw_score" not in keys
    assert "raw_scores_by_path" not in keys


def test_trace_contains_raw_scores():
    ctx = _ctx(unknowns=[_unknown("transit", severity="high")])
    _, trace = build_deterministic_decision(ctx)
    assert trace.raw_scores_by_path
    assert any(step.raw_score is not None for step in trace.steps)


# --------------------------------------------------------------------------- #
# 16. confidence is banded, not floats
# --------------------------------------------------------------------------- #
def test_confidence_is_banded_with_reasons():
    ctx = _ctx(unknowns=[_unknown("classification", severity="high")])
    decision, _ = build_deterministic_decision(ctx)
    report = decision.confidence_report
    assert isinstance(report.band, ConfidenceBand)
    assert report.band is ConfidenceBand.LOW
    assert report.cap_reasons
    assert all(isinstance(reason, str) for reason in report.cap_reasons)
    assert "value" not in report.model_dump()  # no numeric confidence


# --------------------------------------------------------------------------- #
# 17. warnings for blocking gates / critical unknowns
# --------------------------------------------------------------------------- #
def test_warnings_for_blocking_gate_and_critical_unknown():
    ctx = _ctx(
        hard_gates=[_gate("BLK", RequestedMode.road, "blocking", "triggered")],
        unknowns=[_unknown("classification", severity="high")],
    )
    decision, _ = build_deterministic_decision(ctx)
    codes = {w.code for w in decision.must_show_warnings}
    assert "BLOCKING_HARD_GATE" in codes
    assert "CRITICAL_UNKNOWN" in codes
    assert "NOT_FINAL_APPROVAL" in codes


# --------------------------------------------------------------------------- #
# 18. dangerous goods / lithium profile + unknown -> capped + warning
# --------------------------------------------------------------------------- #
def test_dangerous_goods_profile_caps_and_warns():
    ctx = _ctx(
        active_profiles=["dangerous_goods"],
        unknowns=[_unknown("un_number", mode=RequestedMode.road)],
    )
    decision, _ = build_deterministic_decision(ctx)
    band = _band_of(decision, RequestedMode.road)
    assert _ORDER[band] <= _ORDER[LOW]
    codes = {w.code for w in decision.must_show_warnings}
    assert "DANGEROUS_GOODS_UNRESOLVED" in codes
    assert decision.critical_unknowns


def test_lithium_battery_profile_treats_unknown_as_critical():
    ctx = _ctx(
        active_profiles=["lithium_battery"],
        unknowns=[_unknown("watt_hours", mode=RequestedMode.air)],
        candidate_modes=[RequestedMode.air],
    )
    decision, _ = build_deterministic_decision(ctx)
    assert decision.critical_unknowns
    assert _ORDER[_band_of(decision, RequestedMode.air)] <= _ORDER[LOW]


# --------------------------------------------------------------------------- #
# 19. low completeness caps readiness
# --------------------------------------------------------------------------- #
def test_low_completeness_caps_readiness():
    ctx = _ctx(completeness_status="insufficient")
    decision, _ = build_deterministic_decision(ctx)
    band = _band_of(decision, RequestedMode.road)
    assert _ORDER[band] < _ORDER[HIGH]
    assert decision.ranking_type is RankingType.low_data_ranking


# --------------------------------------------------------------------------- #
# 20. no mutation of the context
# --------------------------------------------------------------------------- #
def test_context_is_not_mutated():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        hard_gates=[_gate("G", RequestedMode.road, "high", "triggered")],
        unknowns=[_unknown("transit", severity="high")],
        missing_fields=[_missing("incoterm", "high_value")],
        completeness_status="incomplete_but_usable",
    )
    before = ctx.model_dump()
    build_deterministic_decision(ctx)
    assert ctx.model_dump() == before


# --------------------------------------------------------------------------- #
# extra: candidate empty -> use modes_covered
# --------------------------------------------------------------------------- #
def test_falls_back_to_modes_covered_when_no_candidates():
    ctx = _ctx(candidate_modes=[], modes_covered=[RequestedMode.sea])
    decision, _ = build_deterministic_decision(ctx)
    assert {p.mode for p in decision.ranked_path_families} == {RequestedMode.sea}


# --------------------------------------------------------------------------- #
# Step 4.1 — conflict handling
# --------------------------------------------------------------------------- #
def test_conflict_caps_overall_below_high():
    ctx = _ctx(conflicts=[_conflict("CARGO_UN_MISMATCH")])
    decision, _ = build_deterministic_decision(ctx)
    band = _band_of(decision, RequestedMode.road)
    assert band is SPECIALIZED
    assert _ORDER[decision.overall_readiness_band] < _ORDER[HIGH]


def test_global_conflict_applies_to_all_paths():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        conflicts=[_conflict("CARGO_UN_MISMATCH")],
    )
    decision, _ = build_deterministic_decision(ctx)
    assert {p.mode for p in decision.ranked_path_families} == {RequestedMode.road, RequestedMode.sea}
    assert all(p.readiness_band is SPECIALIZED for p in decision.ranked_path_families)


def test_mode_specific_conflict_applies_only_to_that_mode():
    ctx = _ctx(
        candidate_modes=[RequestedMode.road, RequestedMode.sea],
        conflicts=[_conflict("ROAD_ONLY", mode=RequestedMode.road)],
    )
    decision, _ = build_deterministic_decision(ctx)
    assert _band_of(decision, RequestedMode.road) is SPECIALIZED
    assert _band_of(decision, RequestedMode.sea) is HIGH


def test_conflict_produces_warning():
    ctx = _ctx(conflicts=[_conflict("CARGO_UN_MISMATCH")])
    decision, _ = build_deterministic_decision(ctx)
    codes = {w.code for w in decision.must_show_warnings}
    assert "CONFLICT_PRESENT" in codes


def test_conflict_evidence_refs_appear_in_ranked_path():
    ctx = _ctx(conflicts=[_conflict("CARGO_UN_MISMATCH")])
    decision, _ = build_deterministic_decision(ctx)
    path = decision.ranked_path_families[0]
    assert "conflict:0:CARGO_UN_MISMATCH" in path.evidence_refs
    assert any("conflict:CARGO_UN_MISMATCH=" in cap for cap in path.applied_caps)


def test_conflict_lowers_confidence():
    ctx = _ctx(conflicts=[_conflict("CARGO_UN_MISMATCH")])
    decision, _ = build_deterministic_decision(ctx)
    assert decision.confidence_report.band is ConfidenceBand.LOW
    assert any("conflict:" in reason for reason in decision.confidence_report.cap_reasons)


# --------------------------------------------------------------------------- #
# Step 4.1 — can_wait missing fields
# --------------------------------------------------------------------------- #
def test_can_wait_missing_field_does_not_cap_clean_path():
    ctx = _ctx(missing_fields=[_missing("packaging", "can_wait")])
    decision, _ = build_deterministic_decision(ctx)
    path = decision.ranked_path_families[0]
    assert path.readiness_band is HIGH          # no readiness cap by itself
    assert "packaging" in path.missing_fields   # still visible on the path


def test_high_value_still_caps_below_high():
    ctx = _ctx(missing_fields=[_missing("incoterm", "high_value")])
    decision, _ = build_deterministic_decision(ctx)
    assert _band_of(decision, RequestedMode.road) is MEDIUM


def test_blocking_missing_field_still_strongly_caps():
    ctx = _ctx(missing_fields=[_missing("weight", "blocking")])
    decision, _ = build_deterministic_decision(ctx)
    assert _band_of(decision, RequestedMode.road) in {BLOCKED, SPECIALIZED}


# --------------------------------------------------------------------------- #
# Step 4.1 — trace + raw-score hygiene
# --------------------------------------------------------------------------- #
def test_trace_records_conflict_and_can_wait():
    ctx = _ctx(
        conflicts=[_conflict("CARGO_UN_MISMATCH")],
        missing_fields=[_missing("packaging", "can_wait")],
    )
    _, trace = build_deterministic_decision(ctx)
    step_names = {step.step_name for step in trace.steps}
    assert "conflict_cap" in step_names
    assert "missing_field_noop" in step_names
    assert any("can_wait" in note for note in trace.notes)


def test_decision_has_no_raw_scores_with_conflict():
    ctx = _ctx(
        conflicts=[_conflict("CARGO_UN_MISMATCH")],
        missing_fields=[_missing("packaging", "can_wait")],
    )
    decision, _ = build_deterministic_decision(ctx)
    keys = _all_keys(decision.model_dump())
    assert "raw_score" not in keys
    assert "raw_scores_by_path" not in keys
