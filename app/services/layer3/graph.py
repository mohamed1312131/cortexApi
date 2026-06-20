# app/services/layer3/graph.py
from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, StateGraph

from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import (
    CriticReview,
    CriticVerdict,
    Layer3NextAction,
    Layer3Result,
    Layer3Status,
    SafetyGateReport,
    SafetyGateStatus,
    SafetyViolation,
)
from app.schemas.reasoning_decision import ReadinessBand
from app.services.layer3.agents.analyst_agent import build_analyst_draft
from app.services.layer3.agents.critic_agent import build_critic_review
from app.services.layer3.context_builder import prepare_reasoning_context
from app.services.layer3.decision_builder import build_reasoning_decision
from app.services.layer3.decision_builder import build_blocked_reasoning_decision
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision
from app.services.layer3.routing import (
    ROUTE_BLOCK,
    ROUTE_CLARIFY,
    ROUTE_FETCH,
    ROUTE_PASS,
    ROUTE_REVISE,
    route_after_review,
)
from app.services.layer3.safety_gate import run_safety_gate
from app.services.layer3.state import Layer3State
from app.services.tracing import agent_run_recorder

# Standalone Layer 3 reasoning graph:
#   prepare_reasoning_context -> deterministic_decision_engine -> analyst_agent
#   -> critic_agent_or_skip -> safety_gate -> route_after_review -> terminal builder
#
# The Critic is advisory; the Safety Gate is authoritative and runs regardless of
# whether the Critic ran. The revision loop is bounded by max_revisions. Models are
# injectable for testing. This graph is NOT wired into any route or orchestrator.

_SERIOUS_GATE_SEVERITIES = {"blocking", "high", "critical"}
_DG_PROFILES = {"dangerous_goods", "lithium_battery"}
_ROUTE_ANALYST_OK = "analyst_ok"


def _should_run_critic(state: Layer3State) -> bool:
    context = state["reasoning_context"]
    decision = state["deterministic_decision"]
    draft = state["analyst_draft"]
    if draft.disputes_ranking:
        return True
    if decision.overall_readiness_band is ReadinessBand.HIGH:
        return True
    if any(
        g.status == "triggered" and g.severity in _SERIOUS_GATE_SEVERITIES
        for g in context.hard_gates
    ):
        return True
    if any(u.severity in {"high", "critical"} for u in context.unknowns):
        return True
    if context.conflicts:
        return True
    if set(context.active_profiles) & _DG_PROFILES:
        return True
    return False


def _route_after_analyst(state: Layer3State) -> str:
    if not state.get("analyst_error"):
        return _ROUTE_ANALYST_OK
    if state.get("revision_count", 0) < state.get("max_revisions", 1):
        return ROUTE_REVISE
    return ROUTE_BLOCK


def _analyst_revision_feedback(error: str) -> str:
    if error.startswith("Analyst omitted required narratives:"):
        missing = error.split(":", 1)[1].strip()
        return (
            "Previous output omitted required narratives:\n"
            f"{missing}\n"
            "Return all required narratives in the next JSON output. The number "
            "of narratives must equal the number of required_narratives. Copy "
            "rank, mode, path_family, and evidence_refs from each matching "
            "required_narratives item."
        )
    return (
        f"{error} Copy evidence_refs from the matching ranked path using "
        "rank + mode + path_family. Do not invent evidence_refs."
    )


def _analyst_contract_failed_report(error: str) -> SafetyGateReport:
    return SafetyGateReport(
        status=SafetyGateStatus.block,
        violations=[
            SafetyViolation(
                code="ANALYST_CONTRACT_FAILED",
                severity="blocking",
                message=f"Analyst output failed validation: {error}",
                field_path="analyst_draft",
            )
        ],
        passed=False,
        next_action=Layer3NextAction.block_unsafe,
    )


class Layer3ReasoningGraph:
    """Compiled Layer 3 reasoning graph. Models are captured for the run."""

    def __init__(
        self,
        *,
        analyst_model: BaseChatModel | None = None,
        critic_model: BaseChatModel | None = None,
        max_revisions: int = 1,
    ) -> None:
        self.analyst_model = analyst_model
        self.critic_model = critic_model
        self.max_revisions = max_revisions
        self._graph = self._build_graph()

    def run(
        self,
        *,
        fact_package: FactPackage,
        trace_id: str | None = None,
        conversation_id: str | None = None,
    ) -> Layer3Result:
        final_state = self._graph.invoke(
            {
                "fact_package": fact_package,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
                "revision_count": 0,
                "max_revisions": self.max_revisions,
                "agent_run_order": 0,
            }
        )
        return final_state["result"]

    # ---- nodes ---------------------------------------------------------- #
    @staticmethod
    def _prepare_node(state: Layer3State) -> dict:
        context = prepare_reasoning_context(state["fact_package"])
        return {"reasoning_context": context, "case_id": context.case_id}

    @staticmethod
    def _engine_node(state: Layer3State) -> dict:
        run_order = _next_run_order(state)
        started_at = datetime.now(UTC)
        decision, trace = build_deterministic_decision(
            state["reasoning_context"], trace_id=state.get("trace_id")
        )
        agent_run_recorder.record_success(
            case_id=state["reasoning_context"].case_id,
            conversation_id=state.get("conversation_id"),
            trace_id=state.get("trace_id"),
            layer=3,
            agent_name="layer3_deterministic_decision",
            run_order=run_order,
            input_summary={
                "case_id": state["reasoning_context"].case_id,
                "candidate_modes": [mode.value for mode in state["reasoning_context"].candidate_modes],
                "hard_gate_count": len(state["reasoning_context"].hard_gates),
                "unknown_count": len(state["reasoning_context"].unknowns),
                "conflict_count": len(state["reasoning_context"].conflicts),
            },
            output=decision,
            provider="deterministic",
            model_name="deterministic_decision_engine",
            started_at=started_at,
        )
        return {
            "deterministic_decision": decision,
            "internal_scoring_trace": trace,
            "agent_run_order": run_order,
        }

    def _analyst_node(self, state: Layer3State) -> dict:
        run_order = _next_run_order(state)
        try:
            draft = build_analyst_draft(
                context=state["reasoning_context"],
                decision=state["deterministic_decision"],
                model=self.analyst_model,
                revision_feedback=state.get("analyst_revision_feedback"),
                trace_id=state.get("trace_id"),
                conversation_id=state.get("conversation_id"),
                run_order=run_order,
            )
        except ValueError as exc:
            error = str(exc)
            return {
                "analyst_draft": None,
                "analyst_error": error,
                "analyst_revision_feedback": _analyst_revision_feedback(error),
                "agent_run_order": run_order,
            }
        return {
            "analyst_draft": draft,
            "analyst_error": None,
            "analyst_revision_feedback": None,
            "agent_run_order": run_order,
        }

    def _critic_node(self, state: Layer3State) -> dict:
        run_order = _next_run_order(state)
        if not _should_run_critic(state):
            review = CriticReview(verdict=CriticVerdict.skipped)
            agent_run_recorder.record_skipped(
                case_id=state["reasoning_context"].case_id,
                conversation_id=state.get("conversation_id"),
                trace_id=state.get("trace_id"),
                layer=3,
                agent_name="layer3_critic",
                run_order=run_order,
                input_summary={
                    "case_id": state["reasoning_context"].case_id,
                    "reason": "critic_not_required",
                    "analyst_disputes_ranking": state["analyst_draft"].disputes_ranking,
                },
                output=review,
                provider="deterministic",
                model_name="critic_routing",
            )
            return {"critic_review": review, "agent_run_order": run_order}
        review = build_critic_review(
            context=state["reasoning_context"],
            decision=state["deterministic_decision"],
            analyst_draft=state["analyst_draft"],
            model=self.critic_model,
            trace_id=state.get("trace_id"),
            conversation_id=state.get("conversation_id"),
            run_order=run_order,
        )
        return {"critic_review": review, "agent_run_order": run_order}

    @staticmethod
    def _safety_gate_node(state: Layer3State) -> dict:
        run_order = _next_run_order(state)
        started_at = datetime.now(UTC)
        report = run_safety_gate(
            context=state["reasoning_context"],
            decision=state["deterministic_decision"],
            analyst_draft=state["analyst_draft"],
        )
        agent_run_recorder.record_success(
            case_id=state["reasoning_context"].case_id,
            conversation_id=state.get("conversation_id"),
            trace_id=state.get("trace_id"),
            layer=3,
            agent_name="layer3_safety_gate",
            run_order=run_order,
            input_summary={
                "case_id": state["reasoning_context"].case_id,
                "analyst_present": state.get("analyst_draft") is not None,
                "hard_gate_count": len(state["reasoning_context"].hard_gates),
                "unknown_count": len(state["reasoning_context"].unknowns),
            },
            output=report,
            safety_report=report,
            provider="deterministic",
            model_name="safety_gate",
            started_at=started_at,
        )
        return {
            "safety_gate_report": report,
            "next_action": report.next_action,
            "agent_run_order": run_order,
        }

    @staticmethod
    def _revise_node(state: Layer3State) -> dict:
        return {"revision_count": state.get("revision_count", 0) + 1}

    def _build_pass_node(self, state: Layer3State) -> dict:
        critic = state.get("critic_review")
        # The gate is authoritative: once we route to pass, an advisory critic
        # revise/block (e.g. budget exhausted) is recorded but does not block the
        # decision build, so only forward a pass/skipped critic to the builder.
        critic_for_builder = (
            critic if critic is not None and critic.verdict in (CriticVerdict.pass_, CriticVerdict.skipped) else None
        )
        reasoning_decision = build_reasoning_decision(
            context=state["reasoning_context"],
            decision=state["deterministic_decision"],
            analyst_draft=state["analyst_draft"],
            safety_gate_report=state["safety_gate_report"],
            critic_review=critic_for_builder,
        )
        return {
            "reasoning_decision": reasoning_decision,
            "result": self._result(
                state,
                status=Layer3Status.pass_to_layer4,
                route=ROUTE_PASS,
                reasoning_decision=reasoning_decision,
            ),
        }

    def _build_blocked_node(self, state: Layer3State) -> dict:
        updated_state = state
        if state.get("analyst_error") and state.get("safety_gate_report") is None:
            report = _analyst_contract_failed_report(state["analyst_error"])
            updated_state = {**state, "safety_gate_report": report}

        reasoning_decision = None
        if (
            updated_state.get("reasoning_context") is not None
            and updated_state.get("deterministic_decision") is not None
        ):
            try:
                reasoning_decision = build_blocked_reasoning_decision(
                    context=updated_state["reasoning_context"],
                    decision=updated_state["deterministic_decision"],
                )
            except ValueError as exc:
                updated_state = {
                    **updated_state,
                    "blocked_reasoning_decision_error": str(exc),
                }

        return {
            "safety_gate_report": updated_state.get("safety_gate_report"),
            "result": self._result(
                updated_state,
                status=Layer3Status.blocked,
                route=ROUTE_BLOCK,
                reasoning_decision=reasoning_decision,
            ),
        }

    def _build_clarification_node(self, state: Layer3State) -> dict:
        return {"result": self._result(state, status=Layer3Status.request_user_clarification, route=ROUTE_CLARIFY)}

    def _build_layer2_fetch_node(self, state: Layer3State) -> dict:
        return {"result": self._result(state, status=Layer3Status.request_layer2_fetch, route=ROUTE_FETCH)}

    # ---- result assembly ------------------------------------------------ #
    @staticmethod
    def _result(
        state: Layer3State,
        *,
        status: Layer3Status,
        route: str,
        reasoning_decision=None,
    ) -> Layer3Result:
        decision = state.get("deterministic_decision")
        critic = state.get("critic_review")
        debug: dict = {
            "route": route,
            "revision_count": state.get("revision_count", 0),
            "critic_verdict": critic.verdict.value if critic is not None else None,
        }
        if state.get("analyst_error"):
            debug["analyst_error"] = state["analyst_error"]
        if state.get("blocked_reasoning_decision_error"):
            debug["blocked_reasoning_decision_error"] = state[
                "blocked_reasoning_decision_error"
            ]
        if decision is not None:
            # only the trace REFERENCE (an id) crosses out — never raw scores.
            debug["internal_trace_ref"] = decision.internal_trace_ref
            debug["overall_readiness_band"] = decision.overall_readiness_band.value
            debug["ranking_type"] = decision.ranking_type.value
        case_id = state.get("case_id") or state["fact_package"].case_id
        return Layer3Result(
            case_id=case_id,
            status=status,
            reasoning_decision=reasoning_decision,
            analyst_draft=state.get("analyst_draft"),
            critic_review=critic,
            safety_gate_report=state.get("safety_gate_report"),
            debug=debug,
        )

    # ---- wiring --------------------------------------------------------- #
    def _build_graph(self):
        graph = StateGraph(Layer3State)
        graph.add_node("prepare_reasoning_context", self._prepare_node)
        graph.add_node("deterministic_decision_engine", self._engine_node)
        graph.add_node("analyst_agent", self._analyst_node)
        graph.add_node("critic_agent_or_skip", self._critic_node)
        graph.add_node("safety_gate", self._safety_gate_node)
        graph.add_node("revise", self._revise_node)
        graph.add_node("build_pass_result", self._build_pass_node)
        graph.add_node("build_blocked_result", self._build_blocked_node)
        graph.add_node("build_user_clarification_result", self._build_clarification_node)
        graph.add_node("build_layer2_fetch_result", self._build_layer2_fetch_node)

        graph.set_entry_point("prepare_reasoning_context")
        graph.add_edge("prepare_reasoning_context", "deterministic_decision_engine")
        graph.add_edge("deterministic_decision_engine", "analyst_agent")
        graph.add_conditional_edges(
            "analyst_agent",
            _route_after_analyst,
            {
                _ROUTE_ANALYST_OK: "critic_agent_or_skip",
                ROUTE_REVISE: "revise",
                ROUTE_BLOCK: "build_blocked_result",
            },
        )
        graph.add_edge("critic_agent_or_skip", "safety_gate")
        graph.add_conditional_edges(
            "safety_gate",
            route_after_review,
            {
                ROUTE_PASS: "build_pass_result",
                ROUTE_REVISE: "revise",
                ROUTE_CLARIFY: "build_user_clarification_result",
                ROUTE_FETCH: "build_layer2_fetch_result",
                ROUTE_BLOCK: "build_blocked_result",
            },
        )
        graph.add_edge("revise", "analyst_agent")
        graph.add_edge("build_pass_result", END)
        graph.add_edge("build_blocked_result", END)
        graph.add_edge("build_user_clarification_result", END)
        graph.add_edge("build_layer2_fetch_result", END)
        return graph.compile()


def run_layer3(
    *,
    fact_package: FactPackage,
    trace_id: str | None = None,
    analyst_model: BaseChatModel | None = None,
    critic_model: BaseChatModel | None = None,
    max_revisions: int = 1,
    conversation_id: str | None = None,
) -> Layer3Result:
    """Run the standalone Layer 3 reasoning graph over a FactPackage."""
    graph = Layer3ReasoningGraph(
        analyst_model=analyst_model,
        critic_model=critic_model,
        max_revisions=max_revisions,
    )
    return graph.run(
        fact_package=fact_package,
        trace_id=trace_id,
        conversation_id=conversation_id,
    )


def _next_run_order(state: Layer3State) -> int:
    return state.get("agent_run_order", 0) + 1
