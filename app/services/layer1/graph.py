from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.schemas import IntakeResult
from app.services.layer1.case_state_manager import (
    InMemoryCaseStateStore,
    RedisCaseStateStore,
    case_state_store,
)
from app.services.layer1.nodes import (
    decide_missing_fields,
    extract_shipment_fields,
    load_case_context,
    persist_intake_state,
    route_message_node,
    validate_and_normalize,
)
from app.services.layer1.response_sanitizer import sanitize_intake_result
from app.services.layer1.state import IntakeGraphState


class Layer1StatefulIntakeGraph:
    def __init__(self, store: InMemoryCaseStateStore | RedisCaseStateStore | None = None) -> None:
        self.store = store or case_state_store
        self._graph = self._build_graph()

    def handle_message(
        self,
        *,
        message: str,
        conversation_id: str | None = None,
        case_id: str | None = None,
        user_id: str | None = None,
        company_id: str | None = None,
    ) -> IntakeResult:
        final_state = self._graph.invoke(
            {
                "conversation_id": conversation_id,
                "case_id": case_id,
                "user_id": user_id,
                "company_id": company_id,
                "message": message,
            }
        )
        return sanitize_intake_result(final_state["result"])

    def _build_graph(self):
        graph = StateGraph(IntakeGraphState)
        graph.add_node("load_case_context", lambda state: load_case_context(state, self.store))
        graph.add_node("route_message", route_message_node)
        graph.add_node("extract_shipment_fields", extract_shipment_fields)
        graph.add_node("validate_and_normalize", validate_and_normalize)
        graph.add_node("decide_missing_fields", decide_missing_fields)
        graph.add_node("persist_intake_state", lambda state: persist_intake_state(state, self.store))

        graph.set_entry_point("load_case_context")
        graph.add_edge("load_case_context", "route_message")
        graph.add_edge("route_message", "extract_shipment_fields")
        graph.add_edge("extract_shipment_fields", "validate_and_normalize")
        graph.add_edge("validate_and_normalize", "decide_missing_fields")
        graph.add_edge("decide_missing_fields", "persist_intake_state")
        graph.add_edge("persist_intake_state", END)
        return graph.compile()


layer1_graph = Layer1StatefulIntakeGraph()


def handle_intake_message(
    *,
    message: str,
    conversation_id: str | None = None,
    case_id: str | None = None,
    user_id: str | None = None,
    company_id: str | None = None,
) -> IntakeResult:
    return layer1_graph.handle_message(
        message=message,
        conversation_id=conversation_id,
        case_id=case_id,
        user_id=user_id,
        company_id=company_id,
    )
