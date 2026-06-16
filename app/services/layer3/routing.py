# app/services/layer3/routing.py
from __future__ import annotations

from app.schemas.layer3 import CriticVerdict, Layer3NextAction
from app.services.layer3.state import Layer3State

# Deterministic post-review routing for the Layer 3 graph.
#
# The Safety Gate is authoritative: its block decision wins over the (advisory)
# Critic, and the revision budget (max_revisions) prevents infinite loops.

ROUTE_PASS = "pass_to_layer4"
ROUTE_REVISE = "revise_analyst"
ROUTE_CLARIFY = "request_user_clarification"
ROUTE_FETCH = "request_layer2_fetch"
ROUTE_BLOCK = "block_unsafe"


def route_after_review(state: Layer3State) -> str:
    gate = state["safety_gate_report"]
    critic = state.get("critic_review")
    revision_count = state.get("revision_count", 0)
    max_revisions = state.get("max_revisions", 1)
    next_action = gate.next_action

    # 1. gate block always wins
    if next_action is Layer3NextAction.block_unsafe:
        return ROUTE_BLOCK
    # 2. gate wants a revise but the budget is spent -> block (no infinite loop)
    if next_action is Layer3NextAction.revise_analyst and revision_count >= max_revisions:
        return ROUTE_BLOCK
    # 3. critic block + gate did not pass -> block
    if critic is not None and critic.verdict is CriticVerdict.block and not gate.passed:
        return ROUTE_BLOCK
    # 4. critic asks to revise and budget remains
    if critic is not None and critic.verdict is CriticVerdict.revise and revision_count < max_revisions:
        return ROUTE_REVISE
    # 5. gate asks to revise and budget remains
    if next_action is Layer3NextAction.revise_analyst and revision_count < max_revisions:
        return ROUTE_REVISE
    # 6/7. analyst signals (v1: only honoured when the gate is otherwise satisfied)
    draft = state.get("analyst_draft")
    if draft is not None and draft.user_clarification_questions:
        return ROUTE_CLARIFY
    if draft is not None and draft.layer2_refetch_requests:
        return ROUTE_FETCH
    # 8. gate passed
    if gate.passed:
        return ROUTE_PASS
    # 9. otherwise block
    return ROUTE_BLOCK
