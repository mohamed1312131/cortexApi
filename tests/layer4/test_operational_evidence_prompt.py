from __future__ import annotations

import json
import re

from app.config import settings
from app.schemas.layer3 import Layer3Result, Layer3Status
from app.schemas.layer4 import Layer4ReportRequest
from app.schemas.operational_evidence import (
    CostBoundaryEvidence,
    CostEstimate,
    DocumentEvidence,
    EvidenceStatus,
    GatewayEvidence,
    OperationalEvidence,
    OperationalPathEvidence,
    OperationalRiskEvidence,
    RecommendationRole,
    RiskSeverity,
    RouteLegEvidence,
    RouteLegType,
    ScheduleBoundaryEvidence,
)
from app.schemas.reasoning_decision import (
    ConfidenceBand,
    ConfidenceReport,
    MustShowWarning,
    RankedReadinessOption,
    RankingType,
    ReadinessBand,
    ReasoningDecision,
)
from app.schemas.shipment_request import Commercial, Lane, ModeSelection, RequestedMode, ValidatedShipmentRequest
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.summary import build_layer2_summary
from app.services.layer4.prompt import build_layer4_prompt


def _input_packet(prompt: str) -> dict:
    match = re.search(r"<input_packet>\n(.*)\n</input_packet>", prompt, re.S)
    assert match is not None
    return json.loads(match.group(1))


def _request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-layer4-oe",
        lane=Lane(
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
        commercial=Commercial(ready_date="2026-07-05", deadline="2026-07-30"),
        active_profiles=["general_cargo"],
    )


def _reasoning_decision() -> ReasoningDecision:
    return ReasoningDecision(
        case_id="case-layer4-oe",
        reasoning_decision_id="rd-layer4-oe",
        ranking_type=RankingType.preparation_ranking,
        ranked_readiness_options=[
            RankedReadinessOption(
                rank=1,
                path_family_id="sea_road_preparation",
                mode=RequestedMode.sea,
                readiness_band=ReadinessBand.MEDIUM,
                status="MEDIUM",
                why_ranked_here="Best planning balance in the deterministic ranking.",
                why_not_higher="Requires live gateway, schedule, and quote validation.",
                next_actions=["Confirm Incoterm.", "Request a live forwarder quote."],
            ),
            RankedReadinessOption(
                rank=2,
                path_family_id="air_road_preparation",
                mode=RequestedMode.air,
                readiness_band=ReadinessBand.MEDIUM_LOW,
                status="MEDIUM_LOW",
                why_ranked_here="Faster fallback with cost validation needed.",
                why_not_higher="Higher likely cost and live schedule required.",
                next_actions=["Validate schedule against the requested delivery deadline."],
            ),
            RankedReadinessOption(
                rank=3,
                path_family_id="pure_road_preparation",
                mode=RequestedMode.road,
                readiness_band=ReadinessBand.BLOCKED,
                status="BLOCKED",
                why_ranked_here="Retained for traceability.",
                why_not_higher="Pure road is blocked by corridor feasibility evidence.",
                next_actions=["Do not recommend Pure Road."],
            ),
        ],
        confidence=ConfidenceReport(band=ConfidenceBand.MEDIUM),
        forbidden_claims=["booking confirmed", "customs cleared", "confirmed live schedule"],
    )


def _path_scoped_road_blocker_decision() -> ReasoningDecision:
    decision = _reasoning_decision().model_copy(deep=True)
    decision.confidence = ConfidenceReport(
        band=ConfidenceBand.LOW,
        cap_reasons=["triggered blocking/high hard gate(s)", "missing field: core_shipment.dimensions"],
    )
    decision.must_show_warnings = [
        MustShowWarning(
            code="BLOCKING_HARD_GATE",
            message="Blocking hard gate(s) triggered: ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL.",
        )
    ]
    return decision


def _layer3_result(reasoning_decision: ReasoningDecision | None = None) -> Layer3Result:
    return Layer3Result(
        case_id="case-layer4-oe",
        status=Layer3Status.pass_to_layer4,
        reasoning_decision=reasoning_decision or _reasoning_decision(),
    )


def _operational_evidence() -> OperationalEvidence:
    return OperationalEvidence(
        case_id="case-layer4-oe",
        shipment={"active_profiles": ["general_cargo"]},
        paths=[
            OperationalPathEvidence(
                path_family_id="sea_road_preparation",
                rank=1,
                primary_mode=RequestedMode.sea,
                leg_modes=[RequestedMode.road, RequestedMode.sea, RequestedMode.road],
                display_name="Sea + Road",
                recommendation_role=RecommendationRole.recommended,
                status=EvidenceStatus.requires_validation,
                readiness_band="MEDIUM",
                route_legs=[
                    RouteLegEvidence(leg_type=RouteLegType.first_mile, mode=RequestedMode.road),
                    RouteLegEvidence(leg_type=RouteLegType.main_leg, mode=RequestedMode.sea),
                    RouteLegEvidence(leg_type=RouteLegType.last_mile, mode=RequestedMode.road),
                ],
                gateways=GatewayEvidence(
                    status=EvidenceStatus.unknown,
                    requires_validation=["Export sea gateway could not be resolved from current local evidence."],
                ),
                cost=CostBoundaryEvidence(
                    status=EvidenceStatus.planning_reference,
                    basis="SEA-COST planning-reference examples: lane_benchmark_examples.",
                ),
                schedule=ScheduleBoundaryEvidence(
                    status=EvidenceStatus.requires_validation,
                    ready_date="2026-07-05",
                    deadline="2026-07-30",
                    requires_live_schedule=True,
                ),
                documents=DocumentEvidence(required_documents=["commercial invoice", "bill of lading"]),
                next_actions=["Confirm Incoterm.", "Request a live forwarder quote."],
            ),
            OperationalPathEvidence(
                path_family_id="air_road_preparation",
                rank=2,
                primary_mode=RequestedMode.air,
                leg_modes=[RequestedMode.road, RequestedMode.air, RequestedMode.road],
                display_name="Air + Road",
                recommendation_role=RecommendationRole.fallback,
                status=EvidenceStatus.requires_validation,
                readiness_band="MEDIUM_LOW",
                gateways=GatewayEvidence(
                    status=EvidenceStatus.requires_validation,
                    origin_candidates=["Shenzhen Bao'an International Airport (SZX)"],
                    requires_validation=["Destination airport candidate requires validation."],
                ),
                cost=CostBoundaryEvidence(
                    status=EvidenceStatus.planning_reference,
                    currency="USD",
                    estimate=CostEstimate(low=1000, typical=1250, high=1500),
                    basis="AIR-COST planning rate per chargeable weight",
                ),
                schedule=ScheduleBoundaryEvidence(
                    status=EvidenceStatus.requires_validation,
                    ready_date="2026-07-05",
                    deadline="2026-07-30",
                    requires_live_schedule=True,
                ),
                documents=DocumentEvidence(required_documents=["air waybill", "commercial invoice"]),
            ),
            OperationalPathEvidence(
                path_family_id="pure_road_preparation",
                rank=3,
                primary_mode=RequestedMode.road,
                leg_modes=[RequestedMode.road],
                display_name="Pure Road",
                recommendation_role=RecommendationRole.blocked,
                status=EvidenceStatus.blocked,
                readiness_band="BLOCKED",
                cost=CostBoundaryEvidence(status=EvidenceStatus.unknown),
                schedule=ScheduleBoundaryEvidence(
                    status=EvidenceStatus.blocked,
                    ready_date="2026-07-05",
                    deadline="2026-07-30",
                    requires_live_schedule=True,
                    limitations=["Road timing is not evaluated because the pure road path is blocked."],
                ),
                documents=DocumentEvidence(required_documents=["CMR waybill"]),
                blockers=[
                    OperationalRiskEvidence(
                        severity=RiskSeverity.blocking,
                        message="Intercontinental overland road corridor is impractical",
                    )
                ],
            ),
        ],
    )


def _layer4_request(
    *,
    operational_evidence: OperationalEvidence | None = None,
    reasoning_decision: ReasoningDecision | None = None,
) -> Layer4ReportRequest:
    fact_package = build_fact_package_for_request(_request())
    return Layer4ReportRequest(
        latest_user_message="Compare air, sea, road, and multimodal preparation paths.",
        layer2_summary=build_layer2_summary(fact_package),
        layer3_result=_layer3_result(reasoning_decision),
        operational_evidence=operational_evidence,
    )


def test_layer4_request_accepts_operational_evidence():
    evidence = _operational_evidence()

    request = _layer4_request(operational_evidence=evidence)

    assert request.operational_evidence is evidence
    assert request.reasoning_decision is request.layer3_result.reasoning_decision


def test_layer4_prompt_includes_operational_evidence_paths():
    prompt = build_layer4_prompt(_layer4_request(operational_evidence=_operational_evidence()))

    assert "Sea + Road" in prompt
    assert "Air + Road" in prompt
    assert "Pure Road" in prompt
    assert "planning_reference" in prompt
    assert "2026-07-30" in prompt
    assert "commercial invoice" in prompt
    assert "Intercontinental overland road corridor is impractical" in prompt


def test_layer4_prompt_does_not_depend_on_full_response_include_artifacts(monkeypatch):
    monkeypatch.setattr(settings, "full_response_include_artifacts", False)

    prompt = build_layer4_prompt(_layer4_request(operational_evidence=_operational_evidence()))

    assert '"operational_evidence"' in prompt
    assert "Sea + Road" in prompt
    assert "fact_package" not in prompt


def test_layer4_prompt_truth_hierarchy_mentions_operational_evidence():
    prompt = build_layer4_prompt(_layer4_request(operational_evidence=_operational_evidence()))

    assert "ReasoningDecision is the authority for ranking, readiness bands" in prompt
    assert "OperationalEvidence is the authority for path names, route legs" in prompt
    assert "Use OperationalEvidence path details when present." in prompt


def test_layer4_prompt_forbidden_live_claims():
    prompt = build_layer4_prompt(_layer4_request(operational_evidence=_operational_evidence()))

    assert "confirmed live quote" in prompt
    assert "final booking approval" in prompt
    assert "customs clearance confirmation" in prompt
    assert "invented vessel details" in prompt
    assert "invented flight details" in prompt
    assert "invented truck details" in prompt


def test_layer4_report_does_not_call_road_gate_global():
    evidence = _operational_evidence()
    evidence.global_blockers = [
        OperationalRiskEvidence(
            severity=RiskSeverity.blocking,
            message="Global Blocking Hard Gate: ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL",
        )
    ]

    packet = _input_packet(build_layer4_prompt(_layer4_request(operational_evidence=evidence)))

    assert "Global Blocking Hard Gate" not in json.dumps(packet, ensure_ascii=False)


def test_layer4_report_says_road_blocker_applies_only_to_pure_road():
    packet = _input_packet(
        build_layer4_prompt(
            _layer4_request(
                operational_evidence=_operational_evidence(),
                reasoning_decision=_path_scoped_road_blocker_decision(),
            )
        )
    )
    dumped = json.dumps(packet, ensure_ascii=False)

    assert "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road." in dumped


def test_layer4_report_does_not_say_whole_case_blocked_when_only_road_blocked():
    packet = _input_packet(build_layer4_prompt(_layer4_request(operational_evidence=_operational_evidence())))
    dumped = json.dumps(packet, ensure_ascii=False)

    assert "The case contains a blocked pure-road path, but other paths remain evaluable." in dumped
    assert "whole shipment is blocked" not in dumped


def test_layer4_report_filters_irrelevant_general_cargo_documents():
    evidence = _operational_evidence()
    air_docs = evidence.paths[1].documents
    air_docs.required_documents = [
        "air waybill",
        "commercial invoice",
        "Death certificate",
        "embalming or health certificate if required",
        "coffin/sealing certificate if required",
    ]

    prompt = build_layer4_prompt(_layer4_request(operational_evidence=evidence))

    assert "air waybill" in prompt
    assert "commercial invoice" in prompt
    assert "Death certificate" not in prompt
    assert "embalming" not in prompt
    assert "coffin" not in prompt


def test_layer4_report_filters_irrelevant_profile_unknowns():
    evidence = _operational_evidence()
    evidence.global_unknowns = [
        "cargo_flags.pharma: pharma status unknown",
        "cargo_flags.food_perishable: perishable status unknown",
        "cargo_flags.live_animals: live animals status unknown",
        "core_shipment.dimensions: dimensions missing",
    ]
    evidence.paths[1].risks = [
        OperationalRiskEvidence(message="Evidence gap for pharma: unknown."),
        OperationalRiskEvidence(message="Evidence gap for live_animals: unknown."),
        OperationalRiskEvidence(message="Evidence gap for pallet dimensions."),
    ]

    prompt = build_layer4_prompt(_layer4_request(operational_evidence=evidence))

    assert "pharma status unknown" not in prompt
    assert "perishable status unknown" not in prompt
    assert "live animals status unknown" not in prompt
    assert "Evidence gap for pharma" not in prompt
    assert "Evidence gap for live_animals" not in prompt
    assert "core_shipment.dimensions" in prompt or "pallet dimensions" in prompt


def test_layer4_report_keeps_dimensions_incoterm_gateway_warnings():
    evidence = _operational_evidence()
    evidence.paths[0].next_actions = [
        "Confirm Incoterm.",
        "Confirm pallet dimensions.",
        "Request a live forwarder quote.",
    ]

    prompt = build_layer4_prompt(_layer4_request(operational_evidence=evidence))

    assert "Confirm Incoterm." in prompt
    assert "Confirm pallet dimensions." in prompt
    assert "Export sea gateway could not be resolved from current local evidence." in prompt
    assert "planning_reference" in prompt


def test_layer4_compact_support_excludes_irrelevant_profile_unknowns():
    evidence = _operational_evidence()
    evidence.global_unknowns = [
        "cargo_flags.pharma: pharma status unknown",
        "cargo_flags.food_perishable: perishable status unknown",
        "cargo_flags.live_animals: live animals status unknown",
        "commercial.incoterm: incoterm missing",
    ]

    packet = _input_packet(build_layer4_prompt(_layer4_request(operational_evidence=evidence)))
    dumped = json.dumps(packet["operational_evidence"], ensure_ascii=False)

    assert "pharma status unknown" not in dumped
    assert "perishable status unknown" not in dumped
    assert "live animals status unknown" not in dumped
    assert "incoterm missing" in dumped


def test_layer4_compact_support_marks_mode_specific_hard_gate_as_path_scoped():
    packet = _input_packet(
        build_layer4_prompt(
            _layer4_request(
                operational_evidence=_operational_evidence(),
                reasoning_decision=_path_scoped_road_blocker_decision(),
            )
        )
    )

    assert packet["reasoning_decision"]["path_scoping_note"] == (
        "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road."
    )
    assert "triggered blocking/high hard gate" not in " ".join(
        packet["reasoning_decision"]["confidence"]["cap_reasons"]
    )
