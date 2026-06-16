# app/services/layer3/safety_gate.py
from __future__ import annotations

import re

from app.schemas.layer3 import (
    AnalystDraft,
    DeterministicDecision,
    Layer3NextAction,
    ReasoningContext,
    SafetyGateReport,
    SafetyGateStatus,
    SafetyViolation,
)
from app.schemas.reasoning_decision import ReadinessBand
from app.services.layer3.safety_rules import (
    allowed_evidence_refs,
    analyst_draft_text,
    contains_forbidden_claim,
    contains_raw_score_leakage,
)

# Authoritative, deterministic Layer 3 safety gate.
#
# It re-checks the AnalystDraft against the DeterministicDecision and the
# ReasoningContext FROM SCRATCH — it never assumes the Analyst's own validator ran.
# It does not mutate inputs and never repairs the draft. Unsafe -> violations.
#
# Severity strings: low | medium | high | blocking. A "blocking" severity forces
# status=block; any other violation forces status=revise; none -> pass.

_BLOCKING = "blocking"
_HIGH = "high"
_MEDIUM = "medium"

_SERIOUS_GATE_SEVERITIES = {"blocking", "high", "critical"}
_DG_PROFILES = {"dangerous_goods", "lithium_battery"}

# Phrases that imply top-band readiness (used only when overall band is below HIGH).
_HIGH_IMPLYING_RE = re.compile(r"high readiness|readiness is high|fully ready|highly ready")
# Unqualified "ready" — \bready\b avoids matching "readiness" / "already".
_READY_RE = re.compile(r"\bready\b")


def _is_surfaced(code: str, refs: list[str], blob_lower: str, narrative_refs: set[str]) -> bool:
    """A factor is surfaced if its code or any of its refs appears in the draft."""
    if code and code.lower() in blob_lower:
        return True
    for ref in refs:
        if ref in narrative_refs or ref.lower() in blob_lower:
            return True
    return False


def run_safety_gate(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
    analyst_draft: AnalystDraft,
) -> SafetyGateReport:
    violations: list[SafetyViolation] = []

    def _add(code: str, severity: str, message: str, *, field_path: str | None = None, evidence_refs: list[str] | None = None) -> None:
        violations.append(
            SafetyViolation(
                code=code,
                severity=severity,
                message=message,
                field_path=field_path,
                evidence_refs=evidence_refs or [],
            )
        )

    blob = analyst_draft_text(analyst_draft)
    blob_lower = blob.lower()
    narrative_refs: set[str] = set()
    for narrative in analyst_draft.narratives:
        narrative_refs.update(narrative.evidence_refs)
    allowed = allowed_evidence_refs(context, decision)
    overall = decision.overall_readiness_band

    has_dg = bool(set(context.active_profiles) & _DG_PROFILES)

    # 1. case consistency
    if not (analyst_draft.case_id == decision.case_id == context.case_id):
        _add(
            "CASE_ID_MISMATCH",
            _BLOCKING,
            f"case_id mismatch: draft={analyst_draft.case_id!r} "
            f"decision={decision.case_id!r} context={context.case_id!r}",
        )

    # 2. narrative coverage
    decision_keys = {(p.rank, p.mode, p.path_family) for p in decision.ranked_path_families}
    narrative_keys = [(n.rank, n.mode, n.path_family) for n in analyst_draft.narratives]
    if len(narrative_keys) != len(set(narrative_keys)):
        _add("DUPLICATE_NARRATIVE", _MEDIUM, "Analyst produced duplicate path narratives.")
    narrative_key_set = set(narrative_keys)
    for missing in sorted(map(str, decision_keys - narrative_key_set)):
        _add("OMITTED_NARRATIVE", _MEDIUM, f"Missing narrative for ranked path {missing}.")
    for extra in sorted(map(str, narrative_key_set - decision_keys)):
        _add("EXTRA_NARRATIVE", _MEDIUM, f"Narrative not present in the decision {extra}.")

    # 3. dispute signal (never silently accepted)
    if analyst_draft.disputes_ranking:
        _add(
            "ANALYST_DISPUTES_RANKING",
            _MEDIUM,
            f"Analyst disputes the deterministic ranking: {analyst_draft.dispute_reason!r}",
        )

    # 4. evidence discipline
    for narrative in analyst_draft.narratives:
        field = f"narratives[rank={narrative.rank},mode={narrative.mode.value}]"
        if not narrative.evidence_refs:
            _add("EMPTY_EVIDENCE", _MEDIUM, "Narrative has no evidence_refs.", field_path=field)
            continue
        outside = sorted(set(narrative.evidence_refs) - allowed)
        if outside:
            _add(
                "EVIDENCE_NOT_ALLOWED",
                _BLOCKING,
                f"Narrative cites evidence refs not in decision/context: {outside}",
                field_path=field,
                evidence_refs=outside,
            )

    # 5. hard gate visibility (status read from ReasoningFactor.status, never details)
    for gate in context.hard_gates:
        if gate.status == "triggered" and gate.severity in _SERIOUS_GATE_SEVERITIES:
            if not _is_surfaced(gate.code, gate.evidence_refs, blob_lower, narrative_refs):
                _add(
                    "HIDDEN_HARD_GATE",
                    _BLOCKING,
                    f"Triggered {gate.severity} hard gate {gate.code!r} is not surfaced in the draft.",
                    evidence_refs=list(gate.evidence_refs),
                )

    # 6. unknown visibility (critical/high, or any unknown under a DG/lithium profile)
    for unknown in context.unknowns:
        is_critical = has_dg or unknown.severity in {"high", "critical"}
        if is_critical and not _is_surfaced(unknown.code, unknown.evidence_refs, blob_lower, narrative_refs):
            _add(
                "HIDDEN_UNKNOWN",
                _MEDIUM,
                f"Critical unknown {unknown.code!r} is not surfaced in the draft.",
                evidence_refs=list(unknown.evidence_refs),
            )

    # 7. conflict visibility
    for conflict in context.conflicts:
        if not _is_surfaced(conflict.code, conflict.evidence_refs, blob_lower, narrative_refs):
            _add(
                "CONFLICT_HIDDEN",
                _BLOCKING,
                f"Conflict {conflict.code!r} is not surfaced; readiness cannot be presented confidently.",
                evidence_refs=list(conflict.evidence_refs),
            )

    # 8. readiness-band wording consistency
    if overall is not ReadinessBand.HIGH and _HIGH_IMPLYING_RE.search(blob_lower):
        _add(
            "READINESS_BAND_CONTRADICTION",
            _BLOCKING,
            f"Draft implies HIGH readiness but overall band is {overall.value}.",
        )
    if overall in {ReadinessBand.BLOCKED, ReadinessBand.SPECIALIZED_STUDY_REQUIRED} and _READY_RE.search(
        blob_lower
    ):
        _add(
            "UNQUALIFIED_READY",
            _BLOCKING,
            f"Draft uses unqualified 'ready' but overall band is {overall.value}.",
        )

    # 9. forbidden claims
    forbidden = contains_forbidden_claim(blob)
    if forbidden:
        _add("FORBIDDEN_CLAIM", _BLOCKING, f"Draft contains forbidden claim(s): {forbidden}")

    # 10. raw score leakage
    leaks = contains_raw_score_leakage(blob)
    if leaks:
        _add("RAW_SCORE_LEAKAGE", _BLOCKING, f"Draft leaks internal scoring vocabulary: {leaks}")

    # 11. HIGH readiness safety (deterministic engine should already prevent this)
    if overall is ReadinessBand.HIGH:
        triggered_serious = [
            g
            for g in context.hard_gates
            if g.status == "triggered" and g.severity in _SERIOUS_GATE_SEVERITIES
        ]
        critical_unknowns = [
            u for u in context.unknowns if has_dg or u.severity in {"high", "critical"}
        ]
        if triggered_serious or critical_unknowns or context.conflicts:
            _add(
                "INCONSISTENT_HIGH_READINESS",
                _BLOCKING,
                "Overall readiness is HIGH while triggered serious gates, critical unknowns, "
                "or conflicts exist.",
            )

    return _finalize(violations)


def _finalize(violations: list[SafetyViolation]) -> SafetyGateReport:
    if any(v.severity == _BLOCKING for v in violations):
        return SafetyGateReport(
            status=SafetyGateStatus.block,
            violations=violations,
            passed=False,
            next_action=Layer3NextAction.block_unsafe,
        )
    if violations:
        return SafetyGateReport(
            status=SafetyGateStatus.revise,
            violations=violations,
            passed=False,
            next_action=Layer3NextAction.revise_analyst,
        )
    return SafetyGateReport(
        status=SafetyGateStatus.pass_,
        violations=[],
        passed=True,
        next_action=Layer3NextAction.pass_to_layer4,
    )
