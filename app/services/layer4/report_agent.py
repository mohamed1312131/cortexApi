from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.llm import get_chat_model
from app.core.logging import get_logger
from app.schemas.layer4 import Layer4ReportRequest, Layer4Result
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.llm_response import extract_model_text
from app.services.layer4.prompt import build_layer4_prompt
from app.services.size_observability import log_prompt_size
from app.services.tracing import agent_run_recorder


logger = get_logger(__name__)


def _require_model(model: BaseChatModel | None) -> BaseChatModel:
    model = model or get_chat_model(layer4=True)
    if model is None:
        raise RuntimeError("No LLM configured (LLM_PROVIDER=none). Layer 4 Report Agent requires an LLM.")
    return model


def _modes_reported(request: Layer4ReportRequest) -> list[RequestedMode]:
    seen: set[RequestedMode] = set()
    out: list[RequestedMode] = []

    decision = request.reasoning_decision
    if decision is not None:
        for option in decision.ranked_readiness_options:
            if option.mode not in seen:
                seen.add(option.mode)
                out.append(option.mode)

    if not out:
        if request.layer2_summary is not None:
            valid_modes = {item.value for item in RequestedMode}
            modes = [
                RequestedMode(mode)
                for mode in request.layer2_summary.modes_covered
                if mode in valid_modes
            ]
        elif request.fact_package is not None:
            modes = list(request.fact_package.derived_rollup.modes_covered)
        else:
            modes = []
        for mode in modes:
            if mode not in seen:
                seen.add(mode)
                out.append(mode)

    return out


def _warnings_shown(request: Layer4ReportRequest) -> list[str]:
    decision = request.reasoning_decision
    if decision is None:
        return []
    return [
        f"{warning.code}: {warning.message}"
        for warning in decision.must_show_warnings
    ]


def build_layer4_report(
    request: Layer4ReportRequest,
    *,
    model: BaseChatModel | None = None,
    trace_id: str | None = None,
    conversation_id: str | None = None,
    run_order: int = 0,
) -> Layer4Result:
    """Run the Cortex Layer 4 Transport Readiness Report Agent."""
    chat_model = _require_model(model)
    prompt = build_layer4_prompt(request)
    log_prompt_size(
        logger,
        label="layer4.report",
        prompt=prompt,
        case_id=request.case_id,
    )
    started_at = datetime.now(UTC)
    input_summary = {
        "case_id": request.case_id,
        "report_type": request.report_type.value,
        "layer3_status": request.layer3_result.status.value,
        "has_reasoning_decision": request.reasoning_decision is not None,
        "has_operational_evidence": request.operational_evidence is not None,
    }
    try:
        raw = chat_model.invoke(prompt)
        assistant_message = extract_model_text(raw).strip()
        if not assistant_message:
            raise ValueError("Layer 4 Report Agent returned an empty response.")

        decision = request.reasoning_decision
        result = Layer4Result(
            case_id=request.layer3_result.case_id or request.case_id,
            report_type=request.report_type,
            assistant_message=assistant_message,
            modes_reported=_modes_reported(request),
            warnings_shown=_warnings_shown(request),
            source_reasoning_decision_id=(
                decision.reasoning_decision_id if decision is not None else None
            ),
            debug={
                "layer3_status": request.layer3_result.status.value,
                "layer3_has_reasoning_decision": decision is not None,
            },
        )
    except Exception as exc:
        agent_run_recorder.record_error(
            case_id=request.case_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            layer=4,
            agent_name="layer4_report",
            run_order=run_order,
            input_summary=input_summary,
            prompt=prompt,
            model=chat_model,
            started_at=started_at,
            error=exc,
        )
        raise
    agent_run_recorder.record_success(
        case_id=request.case_id,
        conversation_id=conversation_id,
        trace_id=trace_id,
        layer=4,
        agent_name="layer4_report",
        run_order=run_order,
        input_summary=input_summary,
        output=result,
        prompt=prompt,
        response_text=assistant_message,
        model=chat_model,
        started_at=started_at,
    )
    return result
