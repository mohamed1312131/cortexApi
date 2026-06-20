from app.schemas.operational_evidence import (
    CostBoundaryEvidence,
    DocumentEvidence,
    EvidenceStatus,
    GatewayEvidence,
    HandlingSafetyEvidence,
    OperationalEvidence,
    OperationalPathEvidence,
    RecommendationRole,
    RouteLegEvidence,
    RouteLegType,
    ScheduleBoundaryEvidence,
)
from app.schemas.shipment_request import RequestedMode


def test_operational_evidence_schema_supports_control_tower_paths():
    evidence = OperationalEvidence(
        case_id="case-1",
        shipment={"case_id": "case-1"},
        paths=[
            OperationalPathEvidence(
                path_family_id="sea_road_preparation",
                rank=1,
                primary_mode=RequestedMode.sea,
                leg_modes=[RequestedMode.road, RequestedMode.sea, RequestedMode.road],
                display_name="Sea + Road",
                recommendation_role=RecommendationRole.recommended,
                status=EvidenceStatus.requires_validation,
            ),
            OperationalPathEvidence(
                path_family_id="air_road_preparation",
                rank=2,
                primary_mode=RequestedMode.air,
                leg_modes=[RequestedMode.road, RequestedMode.air, RequestedMode.road],
                display_name="Air + Road",
                recommendation_role=RecommendationRole.fallback,
                status=EvidenceStatus.requires_validation,
            ),
            OperationalPathEvidence(
                path_family_id="rail_multimodal_study",
                rank=3,
                primary_mode=RequestedMode.road,
                leg_modes=[],
                display_name="Rail / Multimodal",
                recommendation_role=RecommendationRole.specialized_study,
                status=EvidenceStatus.requires_validation,
            ),
            OperationalPathEvidence(
                path_family_id="road_preparation",
                rank=4,
                primary_mode=RequestedMode.road,
                leg_modes=[RequestedMode.road],
                display_name="Pure Road",
                recommendation_role=RecommendationRole.blocked,
                status=EvidenceStatus.blocked,
            ),
        ],
    )

    dumped = evidence.model_dump(mode="json")

    assert dumped["evidence_version"] == "operational_evidence.v1"
    assert [path["display_name"] for path in dumped["paths"]] == [
        "Sea + Road",
        "Air + Road",
        "Rail / Multimodal",
        "Pure Road",
    ]
    assert dumped["paths"][0]["leg_modes"] == ["road", "sea", "road"]


def test_operational_path_has_rank_modes_role_and_status():
    path = OperationalPathEvidence(
        path_family_id="sea_road_preparation",
        rank=1,
        primary_mode=RequestedMode.sea,
        leg_modes=[RequestedMode.road, RequestedMode.sea, RequestedMode.road],
        display_name="Sea + Road",
        recommendation_role=RecommendationRole.recommended,
        status=EvidenceStatus.requires_validation,
        cost=CostBoundaryEvidence(),
        schedule=ScheduleBoundaryEvidence(ready_date="2026-07-01", deadline="2026-07-20"),
        documents=DocumentEvidence(),
        handling_safety=HandlingSafetyEvidence(),
        gateways=GatewayEvidence(),
        route_legs=[
            RouteLegEvidence(leg_type=RouteLegType.first_mile, mode=RequestedMode.road),
            RouteLegEvidence(leg_type=RouteLegType.main_leg, mode=RequestedMode.sea),
            RouteLegEvidence(leg_type=RouteLegType.last_mile, mode=RequestedMode.road),
        ],
    )

    assert path.rank == 1
    assert path.primary_mode is RequestedMode.sea
    assert path.leg_modes == [RequestedMode.road, RequestedMode.sea, RequestedMode.road]
    assert path.recommendation_role is RecommendationRole.recommended
    assert path.status is EvidenceStatus.requires_validation
    assert path.cost is not None
    assert path.schedule is not None
    assert path.documents is not None
