from __future__ import annotations

from app.schemas.layer3 import AnalystDraft, AnalystPathNarrative, Layer3Result, Layer3Status
from app.schemas.layer4 import Layer4ReportRequest
from app.schemas.reasoning_decision import ReasoningDecision, RankedReadinessOption
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
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.summary import build_layer2_summary
from app.services.layer3.context_builder import prepare_reasoning_context
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision
from app.services.layer3.prompt_compaction import compact_allowed_evidence_refs
from app.services.operational_evidence.builder import build_operational_evidence


MESSAGE = (
    "We need to ship 4,500 kg of non-dangerous automotive spare parts, 18 CBM, "
    "packed on standard pallets, from Shenzhen, China to Frankfurt, Germany. "
    "Cargo is not dangerous goods, not temperature controlled, not oversized, "
    "and not high value. Ready date is 2026-07-05. Delivery is needed within "
    "25 days. Budget matters, but delay risk should stay moderate. Please "
    "compare air, sea, road, and any realistic multimodal preparation paths."
)


def multimode_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-prompt-budget-multimode",
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
        ready_for_layer_2=True,
    )


def synthetic_analyst_draft(context, decision) -> AnalystDraft:
    allowed = {
        ref["ref_id"] if isinstance(ref, dict) else str(ref)
        for ref in compact_allowed_evidence_refs(context, decision)
    }
    fallback_ref = next(iter(allowed))
    narratives = []
    for path in decision.ranked_path_families:
        refs = [ref for ref in path.evidence_refs if ref in allowed] or [fallback_ref]
        narratives.append(
            AnalystPathNarrative(
                path_family=path.path_family,
                mode=path.mode,
                rank=path.rank,
                why_ranked_here=f"Deterministic readiness ranked {path.path_family} at position {path.rank}.",
                why_not_higher="Live quote, schedule, gateway, and document validation are still required.",
                what_would_improve_readiness=list(path.missing_fields)
                or ["Validate live quote, schedule, and gateway evidence."],
                evidence_refs=refs[:5],
            )
        )
    return AnalystDraft(
        case_id=decision.case_id,
        narratives=narratives,
        overall_summary="Synthetic draft for prompt-budget tests.",
        next_action_summary="Validate live operational evidence before booking.",
    )


def synthetic_reasoning_decision(fact_package, deterministic, analyst_draft) -> ReasoningDecision:
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
        reasoning_decision_id="prompt-budget-synthetic-reasoning-decision",
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
        global_next_actions=["Request live forwarder/carrier validation before execution."],
        must_show_warnings=deterministic.must_show_warnings,
    )


def build_prompt_budget_case():
    fact_package = build_fact_package_for_request(multimode_request())
    layer2_summary = build_layer2_summary(fact_package)
    context = prepare_reasoning_context(fact_package)
    deterministic, _trace = build_deterministic_decision(context, trace_id="prompt-budget-test")
    analyst_draft = synthetic_analyst_draft(context, deterministic)
    reasoning_decision = synthetic_reasoning_decision(fact_package, deterministic, analyst_draft)
    layer3_result = Layer3Result(
        case_id=fact_package.case_id,
        status=Layer3Status.pass_to_layer4,
        reasoning_decision=reasoning_decision,
        analyst_draft=analyst_draft,
        debug={"test": "prompt_budget"},
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
    return {
        "fact_package": fact_package,
        "layer2_summary": layer2_summary,
        "context": context,
        "deterministic": deterministic,
        "analyst_draft": analyst_draft,
        "reasoning_decision": reasoning_decision,
        "layer4_request": layer4_request,
    }
