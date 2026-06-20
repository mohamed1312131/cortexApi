from __future__ import annotations

import inspect
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.llm import get_google_model_name, get_layer_provider
from app.schemas.layer3 import AnalystDraft, AnalystPathNarrative, Layer3Result, Layer3Status
from app.schemas.layer4 import Layer4ReportRequest
from app.schemas.reasoning_decision import (
    ReasoningDecision,
    RankedReadinessOption,
)
from app.schemas.shipment_request import (
    CargoFlags,
    Commercial,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer1.intake_agent import AGENT_PROMPT, _turn_payload, run_intake_agent
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.summary import build_layer2_summary
from app.services.layer3.agents.analyst_agent import (
    ANALYST_PROMPT,
    build_analyst_prompt,
    build_analyst_draft,
)
from app.services.layer3.agents.critic_agent import (
    CRITIC_PROMPT,
    build_critic_prompt,
    build_critic_review,
)
from app.services.layer3.context_builder import prepare_reasoning_context
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision
from app.services.layer3.prompt_compaction import (
    compact_allowed_evidence_refs,
    compact_deterministic_decision_for_prompt,
    compact_path_evidence_refs,
    compact_reasoning_context_for_prompt,
)
from app.services.layer4.prompt import (
    LAYER4_FULL_REPORT_PROMPT,
    _compact_layer3_result,
    _compact_operational_evidence,
    _compact_reasoning_decision,
    build_layer4_prompt,
)
from app.services.layer4.report_agent import build_layer4_report
from app.services.operational_evidence.builder import build_operational_evidence


MESSAGE = (
    "We need to ship 4,500 kg of non-dangerous automotive spare parts, 18 CBM, "
    "packed on standard pallets, from Shenzhen, China to Frankfurt, Germany. "
    "Cargo is not dangerous goods, not temperature controlled, not oversized, "
    "and not high value. Ready date is 2026-07-05. Delivery is needed within "
    "25 days. Budget matters, but delay risk should stay moderate. Please "
    "compare air, sea, road, and any realistic multimodal preparation paths."
)

OUT_DIR = Path("/private/tmp/cortex-prompt-audit")


def _shipment_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-shenzhen-frankfurt-prompt-audit",
        core_shipment=CoreShipment(
            cargo_description="non-dangerous automotive spare parts",
            weight_kg=4500,
            volume_cbm=18,
            packaging="standard pallets",
        ),
        lane=Lane(
            origin_raw="Shenzhen, China",
            destination_raw="Frankfurt, Germany",
            origin_city="Shenzhen",
            destination_city="Frankfurt",
            origin_country="CN",
            destination_country="DE",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.unknown,
            candidate_modes=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
            needs_mode_selection=True,
        ),
        cargo_flags=CargoFlags(
            dangerous_goods=FlagState.no,
            temperature_controlled=FlagState.no,
            oversized=FlagState.no,
            high_value=FlagState.no,
            pharma=FlagState.no,
            food_perishable=FlagState.no,
            live_animals=FlagState.no,
        ),
        active_profiles=["general_cargo"],
        profiles={"general_cargo": {}},
        commercial=Commercial(ready_date="2026-07-05", deadline="2026-07-30"),
        facts_from_user={
            "cargo_description": "non-dangerous automotive spare parts",
            "weight_kg": 4500,
            "volume_cbm": 18,
            "packaging": "standard pallets",
        },
        ready_for_layer_2=True,
        field_confidence={
            "cargo_description": 0.95,
            "weight_kg": 0.95,
            "volume_cbm": 0.95,
        },
        intake_quality_score=0.95,
    )


def _rough_tokens(chars: int) -> int:
    return math.ceil(chars / 4)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _measure_text(text: str) -> dict[str, int]:
    chars = len(text)
    return {"chars": chars, "rough_tokens": _rough_tokens(chars)}


def _recursive_sizes(value: Any, path: str = "$") -> list[dict[str, Any]]:
    sizes = [{"path": path, "chars": _json_size(value), "rough_tokens": _rough_tokens(_json_size(value))}]
    if isinstance(value, dict):
        for key, item in value.items():
            sizes.extend(_recursive_sizes(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            sizes.extend(_recursive_sizes(item, f"{path}[{index}]"))
    return sizes


def _largest_sections(sections: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, value in sections.items():
        if isinstance(value, str):
            chars = len(value)
            rows.append({"path": name, "chars": chars, "rough_tokens": _rough_tokens(chars)})
        else:
            rows.extend(_recursive_sizes(value, name))
    rows.sort(key=lambda item: item["chars"], reverse=True)
    return rows[:limit]


def _line_for_call(function: Any, needle: str) -> str:
    source_file = inspect.getsourcefile(function) or "unknown"
    lines, start = inspect.getsourcelines(function)
    for offset, line in enumerate(lines):
        if needle in line:
            return f"{source_file}:{start + offset}"
    return f"{source_file}:{start}"


def _model_name(*, intake: bool = False, layer3: bool = False, layer4: bool = False) -> str:
    provider = get_layer_provider(intake=intake, layer3=layer3, layer4=layer4)
    if provider in {"google", "gemma", "gemini"}:
        return f"{provider}:{get_google_model_name(intake=intake, layer3=layer3, layer4=layer4)}"
    return provider or "none"


def _synthetic_analyst_draft(context: Any, decision: Any) -> AnalystDraft:
    allowed = {
        ref["ref_id"] if isinstance(ref, dict) else str(ref)
        for ref in compact_allowed_evidence_refs(context, decision)
    }
    narratives = []
    for path in decision.ranked_path_families:
        evidence_refs = [ref for ref in path.evidence_refs if ref in allowed]
        if not evidence_refs:
            evidence_refs = [next(iter(allowed))]
        narratives.append(
            AnalystPathNarrative(
                path_family=path.path_family,
                mode=path.mode,
                rank=path.rank,
                why_ranked_here=(
                    f"Deterministic readiness ranked {path.path_family} at "
                    f"position {path.rank} using Layer 2 evidence refs."
                ),
                why_not_higher=(
                    "Requires validation of live quote, schedule, gateway, and "
                    "remaining evidence gaps before execution."
                ),
                what_would_improve_readiness=list(path.missing_fields)
                or ["Validate live quote, schedule, and gateway evidence."],
                evidence_refs=evidence_refs[:5],
            )
        )
    return AnalystDraft(
        case_id=decision.case_id,
        narratives=narratives,
        overall_summary="Synthetic draft for prompt-size diagnostics only.",
        next_action_summary="Validate live operational evidence before booking.",
    )


def _synthetic_reasoning_decision(fact_package: Any, deterministic: Any, analyst_draft: AnalystDraft) -> ReasoningDecision:
    options = []
    for path in deterministic.ranked_path_families:
        hard_gates = [
            gate
            for block in fact_package.block_responses
            for gate in block.hard_gates
            if gate.mode is path.mode
        ]
        unknowns = [
            unknown
            for block in fact_package.block_responses
            if block.mode is path.mode
            for unknown in block.unknowns
        ][:8]
        narrative = next(item for item in analyst_draft.narratives if item.rank == path.rank)
        options.append(
            RankedReadinessOption(
                rank=path.rank,
                path_family_id=path.path_family,
                mode=path.mode,
                readiness_band=path.readiness_band,
                status=path.readiness_band.value,
                why_ranked_here=narrative.why_ranked_here,
                why_not_higher=narrative.why_not_higher,
                hard_gates=hard_gates,
                unknowns=unknowns,
                next_actions=list(narrative.what_would_improve_readiness),
            )
        )
    return ReasoningDecision(
        case_id=deterministic.case_id,
        reasoning_decision_id="prompt-audit-synthetic-reasoning-decision",
        ranking_type=deterministic.ranking_type,
        ranked_readiness_options=options,
        confidence=deterministic.confidence_report,
        allowed_claims=["This is a preparation-readiness assessment for planning only."],
        forbidden_claims=[
            "booking confirmed",
            "customs cleared",
            "confirmed live quote",
            "confirmed live schedule",
            "final booking approval",
        ],
        global_unknowns=[],
        global_next_actions=["Request live forwarder/carrier validation before execution."],
        must_show_warnings=deterministic.must_show_warnings,
    )


def _extract_input_packet(layer4_prompt: str) -> dict[str, Any]:
    match = re.search(r"<input_packet>\n(.*)\n</input_packet>", layer4_prompt, re.S)
    if not match:
        return {}
    return json.loads(match.group(1))


def _presence(prompt: str) -> dict[str, bool]:
    return {
        "full_graph_state": "cache_status" in prompt and "layer2_artifact_key" in prompt,
        "full_fact_package": '"block_responses"' in prompt or '"fetch_plan"' in prompt,
        "layer2_summary": '"layer2_summary"' in prompt,
        "operational_evidence": '"operational_evidence"' in prompt,
        "reasoning_decision": '"reasoning_decision"' in prompt or "<deterministic_decision>" in prompt,
        "analyst_draft": "<analyst_draft>" in prompt or '"analyst_draft"' in prompt,
        "critic_review": '"critic_review"' in prompt,
        "raw_block_data": '"data_excerpt"' in prompt or '"block_responses"' in prompt,
        "debug_cache_artifact_fields": "cache_status" in prompt or "artifact_refs" in prompt,
    }


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def run_audit() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    request = _shipment_request()
    fact_package = build_fact_package_for_request(request)
    layer2_summary = build_layer2_summary(fact_package)
    context = prepare_reasoning_context(fact_package)
    deterministic, _trace = build_deterministic_decision(context, trace_id="prompt-audit")
    analyst_draft = _synthetic_analyst_draft(context, deterministic)
    reasoning_decision = _synthetic_reasoning_decision(fact_package, deterministic, analyst_draft)
    layer3_result = Layer3Result(
        case_id=fact_package.case_id,
        status=Layer3Status.pass_to_layer4,
        reasoning_decision=reasoning_decision,
        analyst_draft=analyst_draft,
        debug={"diagnostic": "prompt_audit"},
    )
    operational_evidence = build_operational_evidence(
        fact_package=fact_package,
        reasoning_decision=reasoning_decision,
        layer2_summary=layer2_summary,
    )
    layer4_request = Layer4ReportRequest(
        latest_user_message=MESSAGE,
        response_language="auto",
        layer2_summary=layer2_summary,
        layer3_result=layer3_result,
        operational_evidence=operational_evidence,
    )

    layer1_prompt = _turn_payload(MESSAGE, previous_request_json=None, conversation_summary=None)
    analyst_prompt = build_analyst_prompt(context, deterministic)
    critic_prompt = build_critic_prompt(context, deterministic, analyst_draft)
    layer4_prompt = build_layer4_prompt(layer4_request)

    _write(OUT_DIR / "layer1_intake_prompt.txt", layer1_prompt)
    _write(OUT_DIR / "layer3_analyst_prompt.txt", analyst_prompt)
    _write(OUT_DIR / "layer3_critic_prompt.txt", critic_prompt)
    _write(OUT_DIR / "layer4_report_prompt.txt", layer4_prompt)

    allowed_refs = compact_allowed_evidence_refs(context, deterministic)
    required_narratives = [
        {
            "rank": path.rank,
            "mode": path.mode.value,
            "path_family": path.path_family,
            "readiness_band": path.readiness_band.value,
            "evidence_refs": compact_path_evidence_refs(path.evidence_refs),
        }
        for path in deterministic.ranked_path_families
    ]
    path_evidence_refs = [
        {
            "rank": path.rank,
            "mode": path.mode.value,
            "path_family": path.path_family,
            "evidence_refs": compact_path_evidence_refs(path.evidence_refs),
        }
        for path in deterministic.ranked_path_families
    ]
    compact_context = compact_reasoning_context_for_prompt(context, deterministic)
    compact_decision = compact_deterministic_decision_for_prompt(deterministic)
    layer4_packet = _extract_input_packet(layer4_prompt)

    agent_sections = {
        "layer1_intake": {
            "prompt_instructions": AGENT_PROMPT,
            "previous_case_state": "null",
            "conversation_so_far": "(first message of this conversation)",
            "user_message": MESSAGE,
        },
        "layer3_analyst": {
            "prompt_instructions": ANALYST_PROMPT,
            "allowed_evidence_refs": allowed_refs,
            "required_narratives": required_narratives,
            "ranked_path_evidence_refs": path_evidence_refs,
            "reasoning_context": compact_context,
            "deterministic_decision": compact_decision,
        },
        "layer3_critic": {
            "prompt_instructions": CRITIC_PROMPT,
            "allowed_evidence_refs": allowed_refs,
            "reasoning_context": compact_context,
            "deterministic_decision": compact_decision,
            "analyst_draft": analyst_draft.model_dump(mode="json"),
        },
        "layer4_report": {
            "prompt_instructions": LAYER4_FULL_REPORT_PROMPT,
            "layer2_support": layer4_packet.get("layer2_support"),
            "layer3_result": layer4_packet.get("layer3_result"),
            "reasoning_decision": layer4_packet.get("reasoning_decision"),
            "operational_evidence": layer4_packet.get("operational_evidence"),
        },
    }
    prompts = {
        "layer1_intake": layer1_prompt,
        "layer3_analyst": analyst_prompt,
        "layer3_critic": critic_prompt,
        "layer4_report": layer4_prompt,
    }
    calls = {
        "layer1_intake": {
            "file_function": "app/services/layer1/intake_agent.py::run_intake_agent",
            "call_line": _line_for_call(run_intake_agent, ".invoke("),
            "model": _model_name(intake=True),
        },
        "layer3_analyst": {
            "file_function": "app/services/layer3/agents/analyst_agent.py::build_analyst_draft",
            "call_line": _line_for_call(build_analyst_draft, "_generate_draft"),
            "model": _model_name(layer3=True),
        },
        "layer3_critic": {
            "file_function": "app/services/layer3/agents/critic_agent.py::build_critic_review",
            "call_line": _line_for_call(build_critic_review, "_generate_review"),
            "model": _model_name(layer3=True),
        },
        "layer4_report": {
            "file_function": "app/services/layer4/report_agent.py::build_layer4_report",
            "call_line": _line_for_call(build_layer4_report, ".invoke("),
            "model": _model_name(layer4=True),
        },
    }

    summary: dict[str, Any] = {
        "audit_dir": str(OUT_DIR),
        "request_case_id": request.case_id,
        "fact_package": {
            "block_count": len(fact_package.block_responses),
            "completeness": fact_package.completeness.status.value,
            "global_unknowns": len(fact_package.global_unknowns),
            "derived_unknowns": len(fact_package.derived_rollup.unknowns),
        },
        "agents": {},
        "duplication_notes": {
            "request_repeated_in": [
                "layer2_support.request_summary",
                "operational_evidence.shipment",
            ],
            "hard_gates_repeated_in": [
                "layer2_support.hard_gates_total/completeness_reasons",
                "reasoning_decision.ranked_readiness_options[*].hard_gates",
                "operational_evidence.paths[*].blockers",
            ],
            "unknowns_repeated_in": [
                "layer2_support.unknowns_total/missing_fields",
                "reasoning_decision.ranked_readiness_options[*].unknowns",
                "operational_evidence.paths[*].risks/missing_inputs",
            ],
        },
        "llm_calls_found": [
            "Layer 1 intake",
            "Layer 3 Analyst",
            "Layer 3 Critic",
            "Layer 4 Report",
        ],
    }
    for name, prompt in prompts.items():
        char_count = len(prompt)
        agent = {
            **calls[name],
            "prompt_file": str(OUT_DIR / {
                "layer1_intake": "layer1_intake_prompt.txt",
                "layer3_analyst": "layer3_analyst_prompt.txt",
                "layer3_critic": "layer3_critic_prompt.txt",
                "layer4_report": "layer4_report_prompt.txt",
            }[name]),
            "char_count": char_count,
            "rough_token_count": _rough_tokens(char_count),
            "exact_token_count": None,
            "presence": _presence(prompt),
            "largest_sections": _largest_sections(agent_sections[name]),
        }
        summary["agents"][name] = agent

    summary["exact_token_count_note"] = "Not collected; this diagnostic script is local-only and makes no external calls."

    _write(OUT_DIR / "prompt_audit_summary.json", json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main() -> None:
    summary = run_audit()
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
