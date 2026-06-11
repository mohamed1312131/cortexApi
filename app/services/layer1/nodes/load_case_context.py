from __future__ import annotations

from app.services.layer1.case_state_manager import InMemoryCaseStateStore, RedisCaseStateStore
from app.services.layer1.state import IntakeGraphState


def load_case_context(
    state: IntakeGraphState,
    store: InMemoryCaseStateStore | RedisCaseStateStore,
) -> IntakeGraphState:
    case_id = state.get("case_id")
    conversation_id = state.get("conversation_id")
    case_context = store.get(case_id) or store.get_active_for_conversation(conversation_id)
    return {
        **state,
        "case_context": case_context,
        "has_active_case": bool(case_context and case_context.current_shipment_request),
        "memory_hints": [],
        "memory_hints_used": [],
    }

