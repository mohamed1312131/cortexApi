from __future__ import annotations

from uuid import uuid4

from app.core.logging import get_logger, log_layer1_outcome
from app.schemas import (
    CortexNextAction,
    CortexOrchestratorDebug,
    CortexOrchestratorResult,
    IntakeResult,
)
from app.services.layer1 import handle_intake_message
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.trace_writer import build_layer2_trace

logger = get_logger(__name__)


def _is_safe_for_layer_2(layer1: IntakeResult) -> bool:
    """Defensive precondition guard for the Layer 1 -> Layer 2 seam.

    Layer 1 owns the readiness decision; this is only a cheap safety net so that
    unsafe or inconsistent intake can never reach the deterministic fact builder.
    Layer 2 is never asked to decide final readiness here.

    A request may reach Layer 2 only when:
      * Layer 1 produced a structured request (``intake_json`` is not None), and
      * Layer 1 marked it ``ready_for_layer_2``, and
      * no ``blocking`` missing fields remain.

    ``high_value`` and ``can_wait`` gaps are allowed (ready-with-unknowns). If
    Layer 1 ever marks a request ready while blocking gaps remain, we treat it as
    not ready and keep the user in clarification instead of running Layer 2.

    Note: ``rerun_scope`` / ``requires_layer_2_rerun`` remain advisory metadata in
    v1. Partial Layer 2 rerun is intentionally NOT wired into this product path.
    """
    request = layer1.intake_json
    if request is None or not layer1.ready_for_layer_2:
        return False
    if request.missing_fields.blocking:
        return False
    return True


def handle_cortex_message(
    *,
    message: str,
    conversation_id: str | None = None,
    case_id: str | None = None,
    user_id: str | None = None,
    company_id: str | None = None,
    trace_id: str | None = None,
) -> CortexOrchestratorResult:
    trace_id = trace_id or str(uuid4())

    layer1 = handle_intake_message(
        message=message,
        conversation_id=conversation_id,
        case_id=case_id,
        user_id=user_id,
        company_id=company_id,
    )
    log_layer1_outcome(logger, trace_id=trace_id, endpoint="/api/v1/cortex/message", result=layer1)

    if not _is_safe_for_layer_2(layer1):
        logger.info(
            "orchestrator.gate trace_id=%s result=%s layer2_ran=%s",
            trace_id,
            CortexNextAction.ask_user.value,
            False,
        )
        return CortexOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            layer1=layer1,
            layer2=None,
            next_action=CortexNextAction.ask_user,
            debug=CortexOrchestratorDebug(
                layer2_ran=False,
                rerun_scope=layer1.rerun_scope,
                trace_id=trace_id,
            ),
        )

    try:
        layer2 = build_fact_package_for_request(layer1.intake_json)
    except Exception as exc:
        logger.warning(
            "orchestrator.gate trace_id=%s result=%s layer2_ran=%s error=%s",
            trace_id,
            CortexNextAction.error.value,
            True,
            f"{exc.__class__.__name__}: {exc}",
        )
        return CortexOrchestratorResult(
            conversation_id=layer1.conversation_id,
            case_id=layer1.case_id,
            layer1=layer1,
            layer2=None,
            next_action=CortexNextAction.error,
            debug=CortexOrchestratorDebug(
                layer2_ran=True,
                rerun_scope=layer1.rerun_scope,
                error=f"{exc.__class__.__name__}: {exc}",
                trace_id=trace_id,
            ),
        )

    trace = build_layer2_trace(layer2)
    logger.info(
        "orchestrator.gate trace_id=%s result=%s layer2_ran=%s blocks=%d modes=%s",
        trace_id,
        CortexNextAction.show_fact_package.value,
        True,
        len(trace["called_blocks"]),
        [str(mode) for mode in trace["modes_covered"]],
    )
    return CortexOrchestratorResult(
        conversation_id=layer1.conversation_id,
        case_id=layer1.case_id,
        layer1=layer1,
        layer2=layer2,
        next_action=CortexNextAction.show_fact_package,
        debug=CortexOrchestratorDebug(
            layer2_ran=True,
            rerun_scope=layer1.rerun_scope,
            trace_id=trace_id,
        ),
    )
