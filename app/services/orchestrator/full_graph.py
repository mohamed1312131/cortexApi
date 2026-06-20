from __future__ import annotations

import inspect
from uuid import uuid4

from langgraph.graph import END, StateGraph

from app.config import settings
from app.core.logging import get_logger, log_layer1_outcome
from app.schemas.cortex_orchestrator import (
    CortexFullNextAction,
    CortexFullOrchestratorDebug,
    CortexFullOrchestratorResult,
)
from app.schemas.layer3 import Layer3Status
from app.schemas.layer4 import Layer4ReportRequest
from app.services.layer1 import handle_intake_message
from app.services.layer1.case_state_manager import case_state_store
from app.services.layer1.response_sanitizer import sanitize_intake_result
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.summary import build_layer2_summary
from app.services.layer2.trace_writer import build_layer2_trace
from app.services.layer3 import run_layer3
from app.services.layer4 import build_layer4_report
from app.services.operational_evidence.builder import build_operational_evidence
from app.services.orchestrator.artifact_cache import OrchestratorArtifactCache
from app.services.orchestrator.cortex_orchestrator import _is_safe_for_layer_2
from app.services.orchestrator.full_state import CortexFullState
from app.services.size_observability import log_payload_size


logger = get_logger(__name__)

_ROUTE_ASK_USER = "ask_user"
_ROUTE_LAYER2 = "layer2"
_ROUTE_FINAL_REPORT = "final_report"
_ROUTE_LAYER2_ERROR = "layer2_error"
_ROUTE_LAYER3_ERROR = "layer3_error"
_ROUTE_LAYER4_ERROR = "layer4_error"


class CortexFullGraph:
    """Top-level Cortex graph for Layer 1 -> 2 -> 3 -> 4 orchestration."""

    def __init__(self) -> None:
        self._cache = OrchestratorArtifactCache()
        self._graph = self._build_graph()

    def run(
        self,
        *,
        message: str,
        conversation_id: str | None = None,
        case_id: str | None = None,
        user_id: str | None = None,
        company_id: str | None = None,
        trace_id: str | None = None,
    ) -> CortexFullOrchestratorResult:
        trace_id = trace_id or str(uuid4())
        final_state = self._graph.invoke(
            {
                "message": message,
                "conversation_id": conversation_id,
                "case_id": case_id,
                "user_id": user_id,
                "company_id": company_id,
                "trace_id": trace_id,
                "cache_status": {},
            }
        )
        return final_state["result"]

    # ---- nodes ---------------------------------------------------------- #
    @staticmethod
    def _intake_node(state: CortexFullState) -> dict:
        layer1 = handle_intake_message(
            message=state["message"],
            conversation_id=state.get("conversation_id"),
            case_id=state.get("case_id"),
            user_id=state.get("user_id"),
            company_id=state.get("company_id"),
        )
        log_layer1_outcome(
            logger,
            trace_id=state["trace_id"],
            endpoint="/api/v1/cortex/full-message",
            result=layer1,
        )
        version, case_state_cache = _shipment_request_version_for_case(layer1.case_id)
        sanitized = sanitize_intake_result(layer1)
        log_payload_size(
            logger,
            label="full_graph.layer1",
            value=sanitized,
            trace_id=state["trace_id"],
            case_id=layer1.case_id,
        )
        return {
            "layer1": sanitized,
            "case_id": layer1.case_id,
            "shipment_request_version": version,
            "cache_status": _cache_status(
                state,
                "case_state",
                case_state_cache,
            ),
        }

    def _layer2_node(self, state: CortexFullState) -> dict:
        layer1 = state["layer1"]
        version = state.get("shipment_request_version")
        artifact_key = self._cache.layer2_key(
            case_id=layer1.case_id,
            shipment_request_version=version,
        )
        cached = self._cache.get_layer2(
            case_id=layer1.case_id,
            shipment_request_version=version,
        )
        layer2_cache = _cache_read_debug(cached)
        if cached.value is not None:
            logger.info(
                "full_orchestrator.layer2_cache_hit trace_id=%s case_id=%s version=%s",
                state["trace_id"],
                layer1.case_id,
                version,
            )
            log_payload_size(
                logger,
                label="full_graph.layer2.cached",
                value=cached.value,
                trace_id=state["trace_id"],
                case_id=layer1.case_id,
            )
            summary = build_layer2_summary(cached.value)
            log_payload_size(
                logger,
                label="full_graph.layer2_summary.cached",
                value=summary,
                trace_id=state["trace_id"],
                case_id=layer1.case_id,
            )
            return {
                "layer2": cached.value if settings.full_response_include_artifacts else None,
                "layer2_summary": summary,
                "layer2_artifact_key": artifact_key,
                "cache_status": _cache_status(state, "layer2", layer2_cache),
            }

        try:
            layer2 = build_fact_package_for_request(layer1.intake_json)
        except Exception as exc:
            return {
                "error": f"Layer 2 failed: {exc.__class__.__name__}: {exc}",
                "cache_status": _cache_status(state, "layer2", layer2_cache),
            }
        layer2_cache["write"] = self._cache.set_layer2(
            layer2,
            case_id=layer1.case_id,
            shipment_request_version=version,
        )
        trace = build_layer2_trace(layer2)
        logger.info(
            "full_orchestrator.layer2 trace_id=%s case_id=%s blocks=%d modes=%s",
            state["trace_id"],
            layer2.case_id,
            len(trace["called_blocks"]),
            [str(mode) for mode in trace["modes_covered"]],
        )
        log_payload_size(
            logger,
            label="full_graph.layer2",
            value=layer2,
            trace_id=state["trace_id"],
            case_id=layer2.case_id,
        )
        summary = build_layer2_summary(layer2)
        log_payload_size(
            logger,
            label="full_graph.layer2_summary",
            value=summary,
            trace_id=state["trace_id"],
            case_id=layer2.case_id,
        )
        can_drop_layer2 = (
            not settings.full_response_include_artifacts
            and layer2_cache.get("write") == "stored"
        )
        return {
            "layer2": None if can_drop_layer2 else layer2,
            "layer2_summary": summary,
            "layer2_artifact_key": artifact_key,
            "cache_status": _cache_status(state, "layer2", layer2_cache),
        }

    def _layer3_node(self, state: CortexFullState) -> dict:
        version = state.get("shipment_request_version")
        cached = self._cache.get_layer3(
            case_id=state["case_id"],
            shipment_request_version=version,
        )
        layer3_cache = _cache_read_debug(cached)
        if cached.value is not None:
            logger.info(
                "full_orchestrator.layer3_cache_hit trace_id=%s case_id=%s version=%s",
                state["trace_id"],
                state["case_id"],
                version,
            )
            log_payload_size(
                logger,
                label="full_graph.layer3.cached",
                value=cached.value,
                trace_id=state["trace_id"],
                case_id=state["case_id"],
            )
            update = {
                "layer3": cached.value,
                "cache_status": _cache_status(state, "layer3", layer3_cache),
            }
            if not settings.full_response_include_artifacts:
                update["layer2"] = None
            return update

        layer2 = state.get("layer2")
        if layer2 is None:
            layer2_cached = self._cache.get_layer2(
                case_id=state["case_id"],
                shipment_request_version=version,
            )
            layer2_reload_cache = _cache_read_debug(layer2_cached)
            if layer2_cached.value is None:
                return {
                    "error": (
                        "Layer 3 failed: missing Layer 2 fact package in graph "
                        f"state and artifact cache reload was {layer2_cached.status}"
                    ),
                    "cache_status": _cache_status(
                        state,
                        "layer2_reload",
                        layer2_reload_cache,
                    ),
                }
            layer2 = layer2_cached.value
            log_payload_size(
                logger,
                label="full_graph.layer2.reloaded_for_layer3",
                value=layer2,
                trace_id=state["trace_id"],
                case_id=layer2.case_id,
            )

        try:
            layer3 = run_layer3(
                fact_package=layer2,
                trace_id=state["trace_id"],
                conversation_id=state.get("conversation_id"),
            )
        except Exception as exc:
            return {
                "error": f"Layer 3 failed: {exc.__class__.__name__}: {exc}",
                "cache_status": _cache_status(state, "layer3", layer3_cache),
            }
        layer3_cache["write"] = self._cache.set_layer3(
            layer3,
            case_id=state["case_id"],
            shipment_request_version=version,
        )
        logger.info(
            "full_orchestrator.layer3 trace_id=%s case_id=%s status=%s",
            state["trace_id"],
            layer3.case_id,
            layer3.status.value,
        )
        log_payload_size(
            logger,
            label="full_graph.layer3",
            value=layer3,
            trace_id=state["trace_id"],
            case_id=layer3.case_id,
        )
        update = {
            "layer3": layer3,
            "cache_status": _cache_status(state, "layer3", layer3_cache),
        }
        if not settings.full_response_include_artifacts:
            update["layer2"] = None
        return update

    def _layer4_node(self, state: CortexFullState) -> dict:
        layer3 = state.get("layer3")
        layer2_summary = state.get("layer2_summary")
        if layer2_summary is None:
            return {"error": "Layer 4 failed: missing Layer 2 summary in graph state"}
        if layer3 is None:
            return {"error": "Layer 4 failed: missing Layer 3 result in graph state"}
        version = state.get("shipment_request_version")
        layer2_for_evidence = state.get("layer2")
        if layer2_for_evidence is None:
            layer2_cached = self._cache.get_layer2(
                case_id=state["case_id"],
                shipment_request_version=version,
            )
            layer2_reload_cache = _cache_read_debug(layer2_cached)
            if layer2_cached.value is None:
                return {
                    "error": (
                        "Layer 4 failed: missing Layer 2 fact package for "
                        "OperationalEvidence and artifact cache reload was "
                        f"{layer2_cached.status}"
                    ),
                    "cache_status": _cache_status(
                        state,
                        "layer2_reload_for_layer4",
                        layer2_reload_cache,
                    ),
                }
            layer2_for_evidence = layer2_cached.value
            log_payload_size(
                logger,
                label="full_graph.layer2.reloaded_for_layer4",
                value=layer2_for_evidence,
                trace_id=state["trace_id"],
                case_id=layer2_for_evidence.case_id,
            )

        operational_evidence = build_operational_evidence(
            fact_package=layer2_for_evidence,
            reasoning_decision=layer3.reasoning_decision,
            layer2_summary=layer2_summary,
        )
        request = Layer4ReportRequest(
            report_type="full_report",
            latest_user_message=state["message"],
            response_language="auto",
            fact_package=state.get("layer2") if settings.full_response_include_artifacts else None,
            layer2_summary=layer2_summary,
            layer3_result=layer3,
            operational_evidence=operational_evidence,
        )
        log_payload_size(
            logger,
            label="full_graph.layer4_request",
            value=request,
            trace_id=state["trace_id"],
            case_id=state["case_id"],
        )
        cached = self._cache.get_layer4(request, shipment_request_version=version)
        layer4_cache = _cache_read_debug(cached)
        if cached.value is not None:
            logger.info(
                "full_orchestrator.layer4_cache_hit trace_id=%s case_id=%s version=%s",
                state["trace_id"],
                state["case_id"],
                version,
            )
            log_payload_size(
                logger,
                label="full_graph.layer4.cached",
                value=cached.value,
                trace_id=state["trace_id"],
                case_id=state["case_id"],
            )
            update = {
                "layer4": cached.value,
                "cache_status": _cache_status(state, "layer4", layer4_cache),
            }
            if not settings.full_response_include_artifacts:
                update["layer2"] = None
            return update

        try:
            layer4 = _run_layer4_report(
                request,
                trace_id=state["trace_id"],
                conversation_id=state.get("conversation_id"),
                run_order=5,
            )
        except Exception as exc:
            return {
                "error": f"Layer 4 failed: {exc.__class__.__name__}: {exc}",
                "cache_status": _cache_status(state, "layer4", layer4_cache),
            }
        layer4_cache["write"] = self._cache.set_layer4(
            layer4,
            request,
            shipment_request_version=version,
        )
        logger.info(
            "full_orchestrator.layer4 trace_id=%s case_id=%s",
            state["trace_id"],
            layer4.case_id,
        )
        log_payload_size(
            logger,
            label="full_graph.layer4",
            value=layer4,
            trace_id=state["trace_id"],
            case_id=layer4.case_id,
        )
        update = {
            "layer4": layer4,
            "cache_status": _cache_status(state, "layer4", layer4_cache),
        }
        if not settings.full_response_include_artifacts:
            update["layer2"] = None
        return update

    @staticmethod
    def _ask_user_node(state: CortexFullState) -> dict:
        layer1 = state["layer1"]
        result = CortexFullOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            assistant_message=layer1.assistant_message,
            layer1=layer1,
            layer2_summary=state.get("layer2_summary"),
            artifact_refs=_artifact_refs(state),
            layer2=None,
            layer3=None,
            layer4=None,
            next_action=CortexFullNextAction.ask_user,
            debug=CortexFullOrchestratorDebug(
                layer2_ran=False,
                layer3_ran=False,
                layer4_ran=False,
                route=_ROUTE_ASK_USER,
                rerun_scope=layer1.rerun_scope,
                cache=state.get("cache_status", {}),
                trace_id=state["trace_id"],
            ),
        )
        log_payload_size(
            logger,
            label="full_graph.result.ask_user",
            value=result,
            trace_id=state["trace_id"],
            case_id=layer1.case_id,
        )
        return {"result": result}

    @staticmethod
    def _layer2_error_node(state: CortexFullState) -> dict:
        layer1 = state["layer1"]
        result = CortexFullOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            assistant_message=(
                "I understood the shipment well enough to start fact building, "
                "but the fact-building step failed before Cortex could produce a "
                "transport readiness report. Please retry, or check the service logs."
            ),
            layer1=layer1,
            layer2_summary=state.get("layer2_summary"),
            artifact_refs=_artifact_refs(state),
            layer2=None,
            layer3=None,
            layer4=None,
            next_action=CortexFullNextAction.error,
            debug=CortexFullOrchestratorDebug(
                layer2_ran=True,
                layer3_ran=False,
                layer4_ran=False,
                route=_ROUTE_LAYER2_ERROR,
                rerun_scope=layer1.rerun_scope,
                cache=state.get("cache_status", {}),
                trace_id=state["trace_id"],
                error=state.get("error"),
            ),
        )
        log_payload_size(
            logger,
            label="full_graph.result.layer2_error",
            value=result,
            trace_id=state["trace_id"],
            case_id=layer1.case_id,
        )
        return {"result": result}

    @staticmethod
    def _layer3_error_node(state: CortexFullState) -> dict:
        layer1 = state["layer1"]
        result = CortexFullOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            assistant_message=(
                "I built the transport fact package, but the reasoning step failed "
                "before Cortex could validate and summarize the readiness decision. "
                "The Layer 2 fact package is included for debugging; please retry "
                "the reasoning step when the model/provider is available."
            ),
            layer1=layer1,
            layer2_summary=state.get("layer2_summary"),
            artifact_refs=_artifact_refs(state),
            layer2=state.get("layer2") if settings.full_response_include_artifacts else None,
            layer3=None,
            layer4=None,
            next_action=CortexFullNextAction.error,
            debug=CortexFullOrchestratorDebug(
                layer2_ran=True,
                layer3_ran=True,
                layer4_ran=False,
                route=_ROUTE_LAYER3_ERROR,
                rerun_scope=layer1.rerun_scope,
                cache=state.get("cache_status", {}),
                trace_id=state["trace_id"],
                error=state.get("error"),
            ),
        )
        log_payload_size(
            logger,
            label="full_graph.result.layer3_error",
            value=result,
            trace_id=state["trace_id"],
            case_id=layer1.case_id,
        )
        return {"result": result}

    @staticmethod
    def _layer4_error_node(state: CortexFullState) -> dict:
        layer1 = state["layer1"]
        layer3 = state.get("layer3")
        result = CortexFullOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            assistant_message=(
                "Cortex completed fact building and reasoning, but the final report "
                "agent failed before it could produce the user-facing transport "
                "readiness report. The Layer 3 result is included for debugging; "
                "please retry report generation when the model/provider is available."
            ),
            layer1=layer1,
            layer2_summary=state.get("layer2_summary"),
            artifact_refs=_artifact_refs(state),
            layer2=state.get("layer2") if settings.full_response_include_artifacts else None,
            layer3=layer3 if settings.full_response_include_artifacts else None,
            layer4=None,
            next_action=CortexFullNextAction.error,
            debug=CortexFullOrchestratorDebug(
                layer2_ran=True,
                layer3_ran=True,
                layer4_ran=True,
                route=_ROUTE_LAYER4_ERROR,
                rerun_scope=layer1.rerun_scope,
                cache=state.get("cache_status", {}),
                trace_id=state["trace_id"],
                error=state.get("error"),
            ),
        )
        log_payload_size(
            logger,
            label="full_graph.result.layer4_error",
            value=result,
            trace_id=state["trace_id"],
            case_id=layer1.case_id,
        )
        return {"result": result}

    @staticmethod
    def _final_report_node(state: CortexFullState) -> dict:
        layer1 = state["layer1"]
        layer3 = state["layer3"]
        layer4 = state["layer4"]
        result = CortexFullOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            assistant_message=layer4.assistant_message,
            layer1=layer1,
            layer2_summary=state.get("layer2_summary"),
            artifact_refs=_artifact_refs(state),
            layer2=state.get("layer2") if settings.full_response_include_artifacts else None,
            layer3=layer3 if settings.full_response_include_artifacts else None,
            layer4=layer4,
            next_action=_next_action_after_layer3(layer3.status),
            debug=CortexFullOrchestratorDebug(
                layer2_ran=True,
                layer3_ran=True,
                layer4_ran=True,
                route=_ROUTE_FINAL_REPORT,
                rerun_scope=layer1.rerun_scope,
                cache=state.get("cache_status", {}),
                trace_id=state["trace_id"],
            ),
        )
        log_payload_size(
            logger,
            label="full_graph.result.final_report",
            value=result,
            trace_id=state["trace_id"],
            case_id=layer1.case_id,
        )
        update = {"result": result}
        if not settings.full_response_include_artifacts:
            update["layer3"] = None
        return update

    # ---- routes --------------------------------------------------------- #
    @staticmethod
    def _route_after_intake(state: CortexFullState) -> str:
        if _is_safe_for_layer_2(state["layer1"]):
            return _ROUTE_LAYER2
        return _ROUTE_ASK_USER

    @staticmethod
    def _route_after_layer2(state: CortexFullState) -> str:
        if state.get("error"):
            return _ROUTE_LAYER2_ERROR
        return "layer3"

    @staticmethod
    def _route_after_layer3(state: CortexFullState) -> str:
        if state.get("error"):
            return _ROUTE_LAYER3_ERROR
        return "layer4"

    @staticmethod
    def _route_after_layer4(state: CortexFullState) -> str:
        if state.get("error"):
            return _ROUTE_LAYER4_ERROR
        return _ROUTE_FINAL_REPORT

    # ---- wiring --------------------------------------------------------- #
    def _build_graph(self):
        graph = StateGraph(CortexFullState)
        graph.add_node("intake_agent", self._intake_node)
        graph.add_node("layer2_fact_builder", self._layer2_node)
        graph.add_node("layer3_reasoning_graph", self._layer3_node)
        graph.add_node("layer4_report_agent", self._layer4_node)
        graph.add_node("ask_user_result", self._ask_user_node)
        graph.add_node("final_report_result", self._final_report_node)
        graph.add_node("layer2_error_result", self._layer2_error_node)
        graph.add_node("layer3_error_result", self._layer3_error_node)
        graph.add_node("layer4_error_result", self._layer4_error_node)

        graph.set_entry_point("intake_agent")
        graph.add_conditional_edges(
            "intake_agent",
            self._route_after_intake,
            {
                _ROUTE_ASK_USER: "ask_user_result",
                _ROUTE_LAYER2: "layer2_fact_builder",
            },
        )
        graph.add_conditional_edges(
            "layer2_fact_builder",
            self._route_after_layer2,
            {
                _ROUTE_LAYER2_ERROR: "layer2_error_result",
                "layer3": "layer3_reasoning_graph",
            },
        )
        graph.add_conditional_edges(
            "layer3_reasoning_graph",
            self._route_after_layer3,
            {
                _ROUTE_LAYER3_ERROR: "layer3_error_result",
                "layer4": "layer4_report_agent",
            },
        )
        graph.add_conditional_edges(
            "layer4_report_agent",
            self._route_after_layer4,
            {
                _ROUTE_LAYER4_ERROR: "layer4_error_result",
                _ROUTE_FINAL_REPORT: "final_report_result",
            },
        )
        graph.add_edge("ask_user_result", END)
        graph.add_edge("final_report_result", END)
        graph.add_edge("layer2_error_result", END)
        graph.add_edge("layer3_error_result", END)
        graph.add_edge("layer4_error_result", END)
        return graph.compile()


def _next_action_after_layer3(status: Layer3Status) -> CortexFullNextAction:
    if status in (
        Layer3Status.request_user_clarification,
        Layer3Status.request_layer2_fetch,
    ):
        return CortexFullNextAction.ask_user
    if status is Layer3Status.error:
        return CortexFullNextAction.error
    return CortexFullNextAction.show_report


def _shipment_request_version_for_case(case_id: str) -> tuple[int | None, dict]:
    try:
        case_state = case_state_store.get(case_id)
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        logger.warning(
            "full_orchestrator.case_state_version_failed case_id=%s error=%s",
            case_id,
            detail,
        )
        return None, {"read": "error", "error": detail}

    if case_state is None:
        return None, {"read": "miss"}
    return case_state.shipment_request_version, {
        "read": "hit",
        "shipment_request_version": case_state.shipment_request_version,
    }


def _cache_status(state: CortexFullState, artifact: str, value: dict) -> dict:
    cache = dict(state.get("cache_status", {}))
    cache[artifact] = value
    return cache


def _cache_read_debug(cache_read) -> dict:
    out = {"read": cache_read.status}
    if cache_read.key:
        out["key"] = cache_read.key
    if cache_read.error:
        out["error"] = cache_read.error
    return out


def _artifact_refs(state: CortexFullState) -> dict[str, str]:
    refs: dict[str, str] = {}
    layer2_key = state.get("layer2_artifact_key")
    if layer2_key:
        refs["layer2"] = layer2_key
    cache_status = state.get("cache_status", {})
    for artifact in ("layer3", "layer4"):
        value = cache_status.get(artifact)
        if isinstance(value, dict) and isinstance(value.get("key"), str):
            refs[artifact] = value["key"]
    return refs


_GRAPH: CortexFullGraph | None = None


def _get_graph() -> CortexFullGraph:
    # Lazy module-level singleton: the compiled graph and its Redis client are
    # stateless across requests, so we build them once instead of recompiling
    # the graph and opening a new connection pool on every /full-message call.
    # Lazy (not import-time) so importing this module never opens a connection.
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = CortexFullGraph()
    return _GRAPH


def handle_full_cortex_message(
    *,
    message: str,
    conversation_id: str | None = None,
    case_id: str | None = None,
    user_id: str | None = None,
    company_id: str | None = None,
    trace_id: str | None = None,
) -> CortexFullOrchestratorResult:
    return _get_graph().run(
        message=message,
        conversation_id=conversation_id,
        case_id=case_id,
        user_id=user_id,
        company_id=company_id,
        trace_id=trace_id,
    )


def _run_layer4_report(
    request: Layer4ReportRequest,
    *,
    trace_id: str,
    conversation_id: str | None,
    run_order: int,
):
    signature = inspect.signature(build_layer4_report)
    if "trace_id" not in signature.parameters:
        return build_layer4_report(request)
    return build_layer4_report(
        request,
        trace_id=trace_id,
        conversation_id=conversation_id,
        run_order=run_order,
    )
