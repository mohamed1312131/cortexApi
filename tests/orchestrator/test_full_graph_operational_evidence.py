from __future__ import annotations

from types import SimpleNamespace

from app.config import settings
from app.schemas.layer3 import Layer3Result, Layer3Status
from app.schemas.layer4 import Layer4Result
from app.schemas.reasoning_decision import (
    ConfidenceBand,
    ConfidenceReport,
    RankedReadinessOption,
    RankingType,
    ReadinessBand,
    ReasoningDecision,
)
from app.schemas.shipment_request import Commercial, Lane, ModeSelection, RequestedMode, ValidatedShipmentRequest
from app.services.layer2.service import build_fact_package_for_request
from app.services.layer2.summary import build_layer2_summary
from app.services.orchestrator.full_graph import CortexFullGraph


class _FakeCache:
    def __init__(self, layer2):
        self.layer2 = layer2

    def get_layer2(self, **_kwargs):
        return SimpleNamespace(value=self.layer2, status="hit", key="layer2-key", error=None)

    def get_layer4(self, *_args, **_kwargs):
        return SimpleNamespace(value=None, status="disabled", key=None, error=None)

    def set_layer4(self, *_args, **_kwargs):
        return "disabled"


def _request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-full-graph-oe",
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
        case_id="case-full-graph-oe",
        reasoning_decision_id="rd-full-graph-oe",
        ranking_type=RankingType.preparation_ranking,
        ranked_readiness_options=[
            RankedReadinessOption(
                rank=1,
                path_family_id="sea_road_preparation",
                mode=RequestedMode.sea,
                readiness_band=ReadinessBand.MEDIUM,
                status="MEDIUM",
                why_ranked_here="Deterministic ranking placed Sea + Road first.",
                why_not_higher="Requires live validation.",
            ),
            RankedReadinessOption(
                rank=2,
                path_family_id="air_road_preparation",
                mode=RequestedMode.air,
                readiness_band=ReadinessBand.MEDIUM_LOW,
                status="MEDIUM_LOW",
                why_ranked_here="Fallback path.",
                why_not_higher="Requires live validation.",
            ),
            RankedReadinessOption(
                rank=3,
                path_family_id="pure_road_preparation",
                mode=RequestedMode.road,
                readiness_band=ReadinessBand.BLOCKED,
                status="BLOCKED",
                why_ranked_here="Retained for blocked-path traceability.",
                why_not_higher="Pure road is blocked.",
            ),
        ],
        confidence=ConfidenceReport(band=ConfidenceBand.MEDIUM),
    )


def _layer3_result() -> Layer3Result:
    return Layer3Result(
        case_id="case-full-graph-oe",
        status=Layer3Status.pass_to_layer4,
        reasoning_decision=_reasoning_decision(),
    )


def _graph_with_cache(layer2) -> CortexFullGraph:
    graph = CortexFullGraph.__new__(CortexFullGraph)
    graph._cache = _FakeCache(layer2)
    return graph


def test_full_graph_builds_operational_evidence_for_layer4(monkeypatch):
    layer2 = build_fact_package_for_request(_request())
    captured = {}

    def fake_report(request):
        captured["request"] = request
        return Layer4Result(
            case_id=request.case_id,
            assistant_message="stubbed report",
        )

    monkeypatch.setattr("app.services.orchestrator.full_graph.build_layer4_report", fake_report)
    graph = _graph_with_cache(layer2)

    update = graph._layer4_node(
        {
            "message": "Compare air, sea, road, and multimodal preparation paths.",
            "trace_id": "trace-oe",
            "case_id": layer2.case_id,
            "shipment_request_version": 1,
            "cache_status": {},
            "layer2": layer2,
            "layer2_summary": build_layer2_summary(layer2),
            "layer3": _layer3_result(),
        }
    )

    assert "error" not in update
    request = captured["request"]
    assert request.operational_evidence is not None
    assert [path.display_name for path in request.operational_evidence.paths] == [
        "Sea + Road",
        "Air + Road",
        "Pure Road",
    ]


def test_full_graph_operational_evidence_does_not_depend_on_full_response_include_artifacts(monkeypatch):
    layer2 = build_fact_package_for_request(_request())
    captured = {}

    def fake_report(request):
        captured["request"] = request
        return Layer4Result(
            case_id=request.case_id,
            assistant_message="stubbed report",
        )

    monkeypatch.setattr(settings, "full_response_include_artifacts", False)
    monkeypatch.setattr("app.services.orchestrator.full_graph.build_layer4_report", fake_report)
    graph = _graph_with_cache(layer2)

    update = graph._layer4_node(
        {
            "message": "Compare air, sea, road, and multimodal preparation paths.",
            "trace_id": "trace-oe",
            "case_id": layer2.case_id,
            "shipment_request_version": 1,
            "cache_status": {},
            "layer2": None,
            "layer2_summary": build_layer2_summary(layer2),
            "layer3": _layer3_result(),
        }
    )

    assert "error" not in update
    request = captured["request"]
    assert request.fact_package is None
    assert request.operational_evidence is not None
    assert {path.path_family_id for path in request.operational_evidence.paths} >= {
        "sea_road_preparation",
        "air_road_preparation",
        "pure_road_preparation",
    }
