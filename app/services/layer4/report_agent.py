from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.llm import get_chat_model
from app.schemas.layer4 import Layer4ReportRequest, Layer4Result
from app.schemas.shipment_request import RequestedMode
from app.services.layer3.llm_response import extract_model_text
from app.services.layer4.prompt import build_layer4_prompt


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
        for mode in request.fact_package.derived_rollup.modes_covered:
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
) -> Layer4Result:
    """Run the Cortex Layer 4 Transport Readiness Report Agent."""
    chat_model = _require_model(model)
    prompt = build_layer4_prompt(request)
    raw = chat_model.invoke(prompt)
    assistant_message = extract_model_text(raw).strip()
    if not assistant_message:
        raise ValueError("Layer 4 Report Agent returned an empty response.")

    decision = request.reasoning_decision
    return Layer4Result(
        case_id=request.layer3_result.case_id or request.fact_package.case_id,
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

