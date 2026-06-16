# app/services/layer1/graph.py
"""Layer 1 entry point: one agent turn + mechanical plumbing.

The agent (see ``intake_agent``) owns ALL language understanding and intake
policy: extraction, merging, profiles, triage, questions, readiness, and the
user-facing reply. The code below never interprets text — it only:

  1. loads/creates the case (Redis),
  2. runs the agent turn,
  3. diffs previous vs current intake mechanically (changed_fields),
  4. maps changed fields to a Layer 2 rerun scope (static table),
  5. persists and assembles the unchanged ``IntakeResult`` contract.
"""
from __future__ import annotations

from typing import Any

from app.schemas import (
    CaseAction,
    CaseState,
    CaseStatus,
    IntakeDecision,
    IntakeResult,
    ValidatedShipmentRequest,
)
from app.services.layer1.case_state_manager import (
    InMemoryCaseStateStore,
    RedisCaseStateStore,
    append_message_to_summary,
    case_state_store,
    new_case_id,
)
from app.services.layer1.intake_agent import run_intake_agent
from app.services.layer1.response_sanitizer import sanitize_intake_result

_READY_DECISIONS = {
    IntakeDecision.ready_for_layer_2,
    IntakeDecision.ready_for_layer_2_with_unknowns,
    IntakeDecision.update_case_and_rerun,
}

_NO_FACT_ACTIONS = {
    CaseAction.answer_intake_question,
    CaseAction.ask_detail_about_existing_report,
    CaseAction.filter_existing_report,
}


class Layer1AgentIntake:
    """Stateful intake service: agent turn in, ``IntakeResult`` out."""

    def __init__(
        self,
        store: InMemoryCaseStateStore | RedisCaseStateStore | None = None,
        model=None,
    ) -> None:
        self.store = store or case_state_store
        self.model = model

    def handle_message(
        self,
        *,
        message: str,
        conversation_id: str | None = None,
        case_id: str | None = None,
        user_id: str | None = None,
        company_id: str | None = None,
    ) -> IntakeResult:
        existing = self.store.get(case_id) or self.store.get_active_for_conversation(conversation_id)
        working_case_id = existing.case_id if existing else (case_id or new_case_id())
        previous = existing.current_shipment_request if existing else None

        turn = run_intake_agent(
            message,
            case_id=working_case_id,
            previous_request=previous,
            conversation_summary=existing.conversation_summary if existing else None,
            model=self.model,
        )

        # case identity & lifecycle are plumbing, not the agent's:
        if existing is not None and turn.case_action is CaseAction.start_new_case:
            case_state = CaseState(
                case_id=new_case_id(),
                conversation_id=conversation_id,
                user_id=user_id,
                company_id=company_id,
            )
            previous = None
        elif existing is not None:
            case_state = existing
        else:
            case_state = CaseState(
                case_id=working_case_id,
                conversation_id=conversation_id,
                user_id=user_id,
                company_id=company_id,
            )

        current = turn.intake
        current.case_id = case_state.case_id

        if user_id and not case_state.user_id:
            case_state.user_id = user_id
        if company_id and not case_state.company_id:
            case_state.company_id = company_id

        changed_fields = _diff_changed_fields(previous, current)

        rerun_scope = _build_rerun_scope(changed_fields, decision=turn.decision)

        append_message_to_summary(case_state, message)
        if changed_fields or case_state.current_shipment_request is None:
            case_state.shipment_request_version += 1
        case_state.current_shipment_request = current
        case_state.active_profiles = list(current.active_profiles)
        case_state.last_missing_questions = [q.field_target for q in current.questions_to_user]
        case_state.status = _status_for_decision(turn.decision, turn.case_action, case_state.status)
        self.store.save(case_state)

        result = IntakeResult(
            conversation_id=case_state.conversation_id,
            case_id=case_state.case_id,
            case_action=turn.case_action,
            intent=turn.intent,
            decision=turn.decision,
            assistant_message=turn.assistant_message,
            intake_json=current,
            ready_for_layer_2=bool(current.ready_for_layer_2),
            requires_layer_2_rerun=rerun_scope.get("rerun_required", False),
            changed_fields=changed_fields,
            rerun_scope=rerun_scope,
            questions_to_user=list(current.questions_to_user),
        )
        return sanitize_intake_result(result)


layer1_intake = Layer1AgentIntake()


def handle_intake_message(
    *,
    message: str,
    conversation_id: str | None = None,
    case_id: str | None = None,
    user_id: str | None = None,
    company_id: str | None = None,
) -> IntakeResult:
    return layer1_intake.handle_message(
        message=message,
        conversation_id=conversation_id,
        case_id=case_id,
        user_id=user_id,
        company_id=company_id,
    )


# --------------------------------------------------------------------------- #
# mechanical diff (no language, no policy — pure structure comparison)
# --------------------------------------------------------------------------- #
def _diff_changed_fields(
    previous: ValidatedShipmentRequest | None,
    current: ValidatedShipmentRequest,
) -> list[str]:
    baseline = previous or ValidatedShipmentRequest(case_id=current.case_id)
    prev = baseline.model_dump(mode="json")
    cur = current.model_dump(mode="json")

    changed: list[str] = []

    for group in ("core_shipment", "lane", "commercial", "cargo_flags"):
        for field_name, after in cur.get(group, {}).items():
            if prev.get(group, {}).get(field_name) != after:
                changed.append(f"{group}.{field_name}")

    if prev["mode"]["requested_mode"] != cur["mode"]["requested_mode"]:
        changed.append("mode.requested_mode")
    if prev["mode"]["candidate_modes"] != cur["mode"]["candidate_modes"]:
        changed.append("mode.candidate_modes")

    if prev["active_profiles"] != cur["active_profiles"]:
        changed.append("active_profiles")

    profile_names = set(prev.get("profiles", {})) | set(cur.get("profiles", {}))
    for name in sorted(profile_names):
        before_profile = prev.get("profiles", {}).get(name)
        after_profile = cur.get("profiles", {}).get(name)
        if before_profile == after_profile:
            continue
        if isinstance(before_profile, dict) and isinstance(after_profile, dict):
            keys = set(before_profile) | set(after_profile)
            for key in sorted(keys):
                if before_profile.get(key) != after_profile.get(key):
                    changed.append(f"profiles.{name}.{key}")
        else:
            changed.append(f"profiles.{name}")

    return _dedupe(changed)


# --------------------------------------------------------------------------- #
# rerun scope (static field -> Layer 2 scope mapping; no interpretation)
# --------------------------------------------------------------------------- #
_WEIGHT_VOLUME_FIELDS = {
    "core_shipment.weight_kg",
    "core_shipment.volume_cbm",
    "core_shipment.dimensions",
}


def _build_rerun_scope(changed_fields: list[str], *, decision: IntakeDecision) -> dict[str, Any]:
    if not changed_fields:
        return {"changed_fields": [], "rerun_required": False, "scope": []}

    scope: set[str] = set()
    for field in changed_fields:
        if field.startswith("lane."):
            scope.update(
                {
                    "node_resolution",
                    "mode_specific_readiness",
                    "documents_border_rules",
                    "confidence_completeness",
                }
            )
        if field.startswith("profiles.dangerous_goods") or field == "cargo_flags.dangerous_goods":
            scope.update(
                {
                    "dg_checks",
                    "mode_specific_readiness",
                    "documents_border_rules",
                    "confidence_completeness",
                }
            )
        if field.startswith("profiles.lithium_battery"):
            scope.update(
                {
                    "dg_checks",
                    "air_readiness",
                    "documents_border_rules",
                    "confidence_completeness",
                }
            )
        if field.startswith("cargo_flags.") or field.startswith("profiles.") or field == "active_profiles":
            scope.update({"dg_checks", "mode_specific_readiness"})
        if field in _WEIGHT_VOLUME_FIELDS or field.startswith("core_shipment."):
            scope.update(
                {
                    "container_fit",
                    "chargeable_weight",
                    "vehicle_fit",
                    "cost_planning",
                    "mode_specific_readiness",
                    "confidence_completeness",
                }
            )
        if field.startswith("commercial."):
            scope.update(
                {
                    "documents",
                    "cost_planning",
                    "timing_planning",
                    "schedule_readiness",
                    "confidence_completeness",
                }
            )
        if field.startswith("mode."):
            scope.update({"mode_selection", "mode_specific_readiness", "confidence_completeness"})

    rerun_required = decision in _READY_DECISIONS

    return {
        "changed_fields": changed_fields,
        "rerun_required": bool(rerun_required and changed_fields),
        "scope": sorted(scope),
    }


# --------------------------------------------------------------------------- #
# case status lifecycle
# --------------------------------------------------------------------------- #
def _status_for_decision(
    decision: IntakeDecision,
    action: CaseAction,
    current_status: CaseStatus,
) -> CaseStatus:
    if action in _NO_FACT_ACTIONS:
        return current_status
    if decision is IntakeDecision.ask_user:
        return CaseStatus.waiting_for_user_clarification
    if decision in _READY_DECISIONS:
        return CaseStatus.ready_for_layer_2
    return CaseStatus.intake_in_progress


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
