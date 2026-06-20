from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from app.schemas.operational_evidence import EvidenceStatus, RecommendationRole
from app.schemas.reasoning_decision import ConfidenceBand, ReadinessBand
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
from app.services.layer3.context_builder import prepare_reasoning_context
from app.services.layer3.deterministic_decision_engine import build_deterministic_decision
from app.services.operational_evidence.builder import build_operational_evidence


def _request():
    return SimpleNamespace(
        case_id="case-1",
        lane=SimpleNamespace(
            origin_raw="Tunis",
            destination_raw="Marseille",
            origin_city="Tunis",
            destination_city="Marseille",
            origin_country="TN",
            destination_country="FR",
        ),
        commercial=SimpleNamespace(ready_date="2026-07-01", deadline="2026-07-20"),
        cargo_flags=SimpleNamespace(
            dangerous_goods="no",
            temperature_controlled="no",
            oversized="no",
            high_value="no",
            pharma="no",
            food_perishable="no",
            live_animals="no",
        ),
        active_profiles=["general_cargo"],
    )


def _block(block_id, mode, data=None, planning_factors=None, hard_gates=None):
    return SimpleNamespace(
        block_id=block_id,
        mode=mode,
        data=data or {},
        planning_factors=planning_factors or [],
        hard_gates=hard_gates or [],
        provenance=SimpleNamespace(source="test", record_id=f"{block_id}-1", provider_used="mock"),
    )


def _fact_package(*, modes_covered=None, blocks=None, global_hard_gates=None, completeness_reasons=None):
    return SimpleNamespace(
        case_id="case-1",
        request=_request(),
        block_responses=blocks or [],
        derived_rollup=SimpleNamespace(
            modes_covered=modes_covered or [RequestedMode.sea, RequestedMode.air],
            missing_fields=["cargo_value"],
        ),
        global_hard_gates=global_hard_gates or [],
        global_unknowns=[],
        global_missing_fields=[],
        completeness=SimpleNamespace(reasons=completeness_reasons or []),
    )


def _decision(options):
    return SimpleNamespace(
        reasoning_decision_id="rd-1",
        ranked_readiness_options=options,
        confidence=SimpleNamespace(band=ConfidenceBand.MEDIUM),
        global_unknowns=[],
    )


def _option(
    *,
    rank,
    path_family_id,
    mode,
    readiness_band=ReadinessBand.MEDIUM,
    status=None,
    hard_gates=None,
    unknowns=None,
    next_actions=None,
):
    return SimpleNamespace(
        rank=rank,
        path_family_id=path_family_id,
        mode=mode,
        readiness_band=readiness_band,
        status=status or readiness_band.value,
        hard_gates=hard_gates or [],
        unknowns=unknowns or [],
        next_actions=next_actions if next_actions is not None else [f"Validate {path_family_id}"],
    )


def test_build_operational_evidence_from_reasoning_decision_creates_paths():
    fact_package = _fact_package()
    decision = _decision(
        [
            _option(rank=1, path_family_id="sea_road_preparation", mode=RequestedMode.sea),
            _option(rank=2, path_family_id="air_road_preparation", mode=RequestedMode.air),
        ]
    )

    evidence = build_operational_evidence(
        fact_package=fact_package,
        reasoning_decision=decision,
    )

    assert len(evidence.paths) == 2
    assert [path.path_family_id for path in evidence.paths] == [
        "sea_road_preparation",
        "air_road_preparation",
    ]
    assert evidence.paths[0].display_name == "Sea + Road"
    assert evidence.paths[0].confidence_band == "MEDIUM"


def test_build_operational_evidence_marks_first_non_blocked_recommended():
    gate = SimpleNamespace(
        gate_id="ROAD-BLOCK",
        mode=RequestedMode.road,
        severity="blocking",
        status="triggered",
        message="Road corridor unavailable",
        source_block="ROAD-C",
        basis="corridor screening",
    )
    fact_package = _fact_package(modes_covered=[RequestedMode.road, RequestedMode.sea, RequestedMode.air])
    decision = _decision(
        [
            _option(
                rank=1,
                path_family_id="pure_road_preparation",
                mode=RequestedMode.road,
                readiness_band=ReadinessBand.BLOCKED,
                hard_gates=[gate],
            ),
            _option(rank=2, path_family_id="sea_road_preparation", mode=RequestedMode.sea),
            _option(rank=3, path_family_id="air_road_preparation", mode=RequestedMode.air),
        ]
    )

    evidence = build_operational_evidence(
        fact_package=fact_package,
        reasoning_decision=decision,
    )
    by_family = {path.path_family_id: path for path in evidence.paths}

    assert by_family["pure_road_preparation"].recommendation_role is RecommendationRole.blocked
    assert by_family["pure_road_preparation"].status is EvidenceStatus.blocked
    assert by_family["pure_road_preparation"].blockers[0].message == "Road corridor unavailable"
    assert by_family["sea_road_preparation"].recommendation_role is RecommendationRole.recommended
    assert by_family["air_road_preparation"].recommendation_role is RecommendationRole.fallback


def test_build_operational_evidence_does_not_mutate_inputs():
    fact_package = _fact_package()
    decision = _decision(
        [
            _option(rank=1, path_family_id="sea_road_preparation", mode=RequestedMode.sea),
            _option(
                rank=2,
                path_family_id="air_road_preparation",
                mode=RequestedMode.air,
                unknowns=[SimpleNamespace(field="deadline", reason="not confirmed", impact="schedule risk")],
            ),
        ]
    )
    fact_package_before = deepcopy(fact_package)
    decision_before = deepcopy(decision)

    build_operational_evidence(fact_package=fact_package, reasoning_decision=decision)

    assert fact_package == fact_package_before
    assert decision == decision_before


def test_build_operational_evidence_includes_ready_date_deadline_in_schedule():
    fact_package = _fact_package()
    decision = _decision(
        [
            _option(rank=1, path_family_id="sea_road_preparation", mode=RequestedMode.sea),
        ]
    )

    evidence = build_operational_evidence(
        fact_package=fact_package,
        reasoning_decision=decision,
    )

    assert evidence.paths[0].schedule is not None
    assert evidence.paths[0].schedule.ready_date == "2026-07-01"
    assert evidence.paths[0].schedule.deadline == "2026-07-20"


def _standard_decision():
    return _decision(
        [
            _option(rank=1, path_family_id="sea_road_preparation", mode=RequestedMode.sea),
            _option(rank=2, path_family_id="air_road_preparation", mode=RequestedMode.air),
            _option(rank=3, path_family_id="pure_road_preparation", mode=RequestedMode.road),
        ]
    )


def _by_family(evidence):
    return {path.path_family_id: path for path in evidence.paths}


def test_operational_evidence_populates_documents_by_path():
    fact_package = _fact_package(
        modes_covered=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
        blocks=[
            _block("SEA-F", RequestedMode.sea, {"documents": ["commercial_invoice", "bill_of_lading"]}),
            _block("AIR-F", RequestedMode.air, {"required_documents": ["air_waybill", "commercial_invoice"]}),
            _block("ROAD-F", RequestedMode.road, {"documents": ["cmr_waybill", "commercial_invoice"]}),
        ],
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    by_family = _by_family(evidence)

    assert by_family["sea_road_preparation"].documents.required_documents == ["commercial_invoice", "bill_of_lading"]
    assert by_family["air_road_preparation"].documents.required_documents == ["air_waybill", "commercial_invoice"]
    assert by_family["pure_road_preparation"].documents.required_documents == ["cmr_waybill", "commercial_invoice"]


def test_general_cargo_air_documents_exclude_human_remains_documents():
    fact_package = _fact_package(
        blocks=[
            _block(
                "AIR-F",
                RequestedMode.air,
                {
                    "required_documents": [
                        "air waybill",
                        "commercial invoice",
                        "Death certificate",
                        "embalming or health certificate if required",
                        "coffin/sealing certificate if required",
                    ]
                },
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    docs = _by_family(evidence)["air_road_preparation"].documents.required_documents
    joined = " ".join(docs).lower()

    assert "air waybill" in docs
    assert "commercial invoice" in docs
    assert "death certificate" not in joined
    assert "embalming" not in joined
    assert "coffin" not in joined


def test_general_cargo_documents_are_profile_relevant():
    fact_package = _fact_package(
        blocks=[
            _block(
                "AIR-F",
                RequestedMode.air,
                {
                    "required_documents": [
                        "commercial invoice",
                        "packing list",
                        "air waybill",
                        "animal health certificate",
                        "veterinary certificate",
                        "pharma product certificate",
                        "phytosanitary certificate",
                    ]
                },
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    docs = _by_family(evidence)["air_road_preparation"].documents.required_documents
    joined = " ".join(docs).lower()

    assert "commercial invoice" in docs
    assert "packing list" in docs
    assert "air waybill" in docs
    assert "animal" not in joined
    assert "veterinary" not in joined
    assert "pharma" not in joined
    assert "phytosanitary" not in joined


def test_operational_evidence_populates_air_cost_estimate():
    fact_package = _fact_package(
        blocks=[
            _block(
                "AIR-COST",
                RequestedMode.air,
                {
                    "estimated_cost_usd": {"low": 1000, "typical": 1250, "high": 1500},
                    "rate_basis": "planning rate per chargeable weight",
                    "currency": "USD",
                    "transit_days_door_to_door": {"low": 4, "typical": 6, "high": 8},
                },
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    cost = _by_family(evidence)["air_road_preparation"].cost

    assert cost.status is EvidenceStatus.planning_reference
    assert cost.currency == "USD"
    assert cost.basis == "planning rate per chargeable weight"
    assert cost.estimate.low == 1000
    assert cost.estimate.typical == 1250
    assert cost.estimate.high == 1500


def test_operational_evidence_maps_sea_cost_benchmark_as_planning_reference():
    fact_package = _fact_package(
        blocks=[
            _block(
                "SEA-COST",
                RequestedMode.sea,
                {
                    "lane_benchmark_examples": [{"route": "Asia-Europe benchmark"}],
                    "surcharge_examples": [{"name": "BAF"}],
                    "local_charge_examples": [{"name": "THC"}],
                },
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    cost = _by_family(evidence)["sea_road_preparation"].cost

    assert cost.status is EvidenceStatus.planning_reference
    assert cost.estimate is None
    assert "SEA-COST planning-reference examples" in cost.basis
    assert any("no normalized low/typical/high estimate" in item for item in cost.limitations)


def test_operational_evidence_copies_ready_date_deadline_to_all_paths():
    evidence = build_operational_evidence(
        fact_package=_fact_package(modes_covered=[RequestedMode.sea, RequestedMode.air, RequestedMode.road]),
        reasoning_decision=_standard_decision(),
    )

    for path in evidence.paths:
        assert path.schedule.ready_date == "2026-07-01"
        assert path.schedule.deadline == "2026-07-20"


def test_operational_evidence_maps_road_gate_only_to_pure_road_blockers():
    gate = SimpleNamespace(
        gate_id="ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL",
        mode=RequestedMode.road,
        severity="blocking",
        status="triggered",
        message="Intercontinental overland road corridor is impractical",
        source_block="ROAD-C",
        basis="corridor viability",
    )
    decision = _decision(
        [
            _option(rank=1, path_family_id="sea_road_preparation", mode=RequestedMode.sea),
            _option(rank=2, path_family_id="air_road_preparation", mode=RequestedMode.air),
            _option(
                rank=3,
                path_family_id="pure_road_preparation",
                mode=RequestedMode.road,
                readiness_band=ReadinessBand.BLOCKED,
                hard_gates=[gate],
            ),
        ]
    )

    evidence = build_operational_evidence(
        fact_package=_fact_package(modes_covered=[RequestedMode.sea, RequestedMode.air, RequestedMode.road]),
        reasoning_decision=decision,
    )
    by_family = _by_family(evidence)

    assert by_family["pure_road_preparation"].blockers[0].message == "Intercontinental overland road corridor is impractical"
    assert by_family["sea_road_preparation"].blockers == []
    assert by_family["air_road_preparation"].blockers == []


def test_mode_specific_road_gate_is_not_global_blocker():
    gate = SimpleNamespace(
        gate_id="ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL",
        mode=RequestedMode.road,
        severity="blocking",
        status="triggered",
        message="Intercontinental overland road corridor is impractical",
        source_block="ROAD-C",
        basis="corridor viability",
    )

    evidence = build_operational_evidence(
        fact_package=_fact_package(
            modes_covered=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
            global_hard_gates=[gate],
            completeness_reasons=["blocking hard gate triggered"],
        ),
        reasoning_decision=_standard_decision(),
    )

    assert evidence.global_blockers == []
    assert "The case contains a blocked pure-road path, but other paths remain evaluable." in evidence.global_limitations
    assert "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road." in evidence.global_limitations


def test_operational_evidence_route_leg_placeholders_for_sea_air_road():
    evidence = build_operational_evidence(
        fact_package=_fact_package(modes_covered=[RequestedMode.sea, RequestedMode.air, RequestedMode.road]),
        reasoning_decision=_standard_decision(),
    )
    by_family = _by_family(evidence)

    assert [leg.leg_type.value for leg in by_family["sea_road_preparation"].route_legs] == [
        "first_mile",
        "main_leg",
        "last_mile",
    ]
    assert [leg.mode for leg in by_family["sea_road_preparation"].route_legs] == [
        RequestedMode.road,
        RequestedMode.sea,
        RequestedMode.road,
    ]
    assert [leg.mode for leg in by_family["air_road_preparation"].route_legs] == [
        RequestedMode.road,
        RequestedMode.air,
        RequestedMode.road,
    ]
    assert [leg.leg_type.value for leg in by_family["pure_road_preparation"].route_legs] == ["main_leg"]
    assert [leg.mode for leg in by_family["pure_road_preparation"].route_legs] == [RequestedMode.road]


def test_operational_evidence_does_not_invent_gateway_when_missing():
    evidence = build_operational_evidence(
        fact_package=_fact_package(blocks=[]),
        reasoning_decision=_standard_decision(),
    )
    by_family = _by_family(evidence)

    assert by_family["sea_road_preparation"].gateways.origin_candidates == []
    assert by_family["sea_road_preparation"].gateways.destination_candidates == []
    assert by_family["air_road_preparation"].gateways.origin_candidates == []
    assert by_family["air_road_preparation"].gateways.destination_candidates == []


def test_air_gateway_does_not_mirror_origin_as_destination():
    fact_package = _fact_package(
        blocks=[
            _block(
                "AIR-C",
                RequestedMode.air,
                {
                    "airport_name": "Shenzhen Bao'an International Airport",
                    "airport_code": "SZX",
                    "known_handlers": "general cargo handlers available",
                },
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    gateways = _by_family(evidence)["air_road_preparation"].gateways

    assert any("SZX" in candidate for candidate in gateways.origin_candidates)
    assert not any("SZX" in candidate for candidate in gateways.destination_candidates)
    assert "Destination airport candidate requires validation." in gateways.requires_validation


def test_sea_gateway_unresolved_has_validation_message():
    fact_package = _fact_package(blocks=[_block("SEA-C", RequestedMode.sea, {})])

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=_standard_decision())
    gateways = _by_family(evidence)["sea_road_preparation"].gateways

    assert gateways.origin_candidates == []
    assert gateways.destination_candidates == []
    assert "Export sea gateway could not be resolved from current local evidence." in gateways.requires_validation


def test_general_cargo_filters_irrelevant_profile_unknowns():
    unknowns = [
        SimpleNamespace(field="cargo_flags.pharma", reason="not relevant", impact="profile noise"),
        SimpleNamespace(field="cargo_flags.food_perishable", reason="not relevant", impact="profile noise"),
        SimpleNamespace(field="cargo_flags.live_animals", reason="not relevant", impact="profile noise"),
        SimpleNamespace(field="core_shipment.dimensions", reason="not confirmed", impact="quote precision"),
    ]
    decision = _decision(
        [
            _option(
                rank=1,
                path_family_id="sea_road_preparation",
                mode=RequestedMode.sea,
                unknowns=unknowns,
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=_fact_package(), reasoning_decision=decision)
    path = evidence.paths[0]
    risk_text = " ".join(risk.message for risk in path.risks)

    assert "pharma" not in risk_text
    assert "food_perishable" not in risk_text
    assert "live_animals" not in risk_text
    assert "cargo_flags.pharma" not in path.missing_inputs
    assert "cargo_flags.food_perishable" not in path.missing_inputs
    assert "cargo_flags.live_animals" not in path.missing_inputs
    assert "core_shipment.dimensions" in path.missing_inputs


def test_next_actions_are_user_friendly():
    decision = _decision(
        [
            _option(
                rank=1,
                path_family_id="sea_road_preparation",
                mode=RequestedMode.sea,
                next_actions=[
                    "commercial.incoterm",
                    "cargo_flags.pharma",
                    "core_shipment.dimensions",
                    "cargo.hs_code",
                    "schedule.deadline",
                    "cost.live_quote",
                ],
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=_fact_package(), reasoning_decision=decision)
    actions = evidence.paths[0].next_actions

    assert "Confirm Incoterm." in actions
    assert "Confirm pallet dimensions." in actions
    assert "Confirm HS code or commodity classification." in actions
    assert "Validate schedule against the requested delivery deadline." in actions
    assert "Request a live forwarder quote." in actions
    assert all("commercial.incoterm" not in action for action in actions)
    assert all("cargo_flags.pharma" not in action for action in actions)
    assert all("core_shipment.dimensions" not in action for action in actions)


def test_road_blocked_schedule_limitations_are_human_readable():
    gate = SimpleNamespace(
        gate_id="ROAD_C_INTERCONTINENTAL_OVERLAND_IMPRACTICAL",
        mode=RequestedMode.road,
        severity="blocking",
        status="triggered",
        message="Intercontinental overland road corridor is impractical",
        source_block="ROAD-C",
        basis="corridor viability",
    )
    fact_package = _fact_package(
        modes_covered=[RequestedMode.sea, RequestedMode.air, RequestedMode.road],
        blocks=[
            _block("ROAD-C", RequestedMode.road, hard_gates=[gate]),
            _block(
                "ROAD-F",
                RequestedMode.road,
                {
                    "realistic_transit_reference": {
                        "model_status": "blocked",
                        "assumptions": ["do not expose"],
                        "hard_gate_flags": ["do not expose"],
                    },
                    "border_buffer_examples": {"hard_gate_flags": ["do not expose"]},
                },
            ),
        ],
    )
    decision = _decision(
        [
            _option(
                rank=1,
                path_family_id="pure_road_preparation",
                mode=RequestedMode.road,
                readiness_band=ReadinessBand.BLOCKED,
                hard_gates=[gate],
            )
        ]
    )

    evidence = build_operational_evidence(fact_package=fact_package, reasoning_decision=decision)
    schedule = evidence.paths[0].schedule

    assert "Pure road is blocked by corridor feasibility evidence." in schedule.limitations
    assert "Road timing is not evaluated because the pure road path is blocked." in schedule.limitations
    joined = " ".join(schedule.limitations)
    assert "model_status" not in joined
    assert "assumptions" not in joined
    assert "hard_gate_flags" not in joined


def test_operational_evidence_full_multimode_case_snapshot():
    request = ValidatedShipmentRequest(
        case_id="case-cn-de-multimode",
        core_shipment=CoreShipment(
            cargo_description="non-dangerous automotive spare parts",
            weight_kg=4500,
            volume_cbm=18,
            packaging="standard pallets",
        ),
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
    )
    package = build_fact_package_for_request(request)
    ctx = prepare_reasoning_context(package)
    deterministic_decision, _ = build_deterministic_decision(ctx)
    options = []
    for path in deterministic_decision.ranked_path_families:
        path_unknowns = [
            SimpleNamespace(field=factor.code, reason=factor.label, impact=factor.details)
            for factor in [*ctx.unknowns, *ctx.missing_fields]
            if factor.mode in {None, path.mode}
        ]
        options.append(
            _option(
                rank=path.rank,
                path_family_id=path.path_family,
                mode=path.mode,
                readiness_band=path.readiness_band,
                hard_gates=[
                    gate
                    for block in package.block_responses
                    for gate in block.hard_gates
                    if gate.mode is path.mode
                ],
                unknowns=path_unknowns,
                next_actions=list(path.missing_fields),
            )
        )

    evidence = build_operational_evidence(
        fact_package=package,
        reasoning_decision=_decision(options),
    )
    by_family = _by_family(evidence)

    assert set(by_family) >= {
        "sea_road_preparation",
        "air_road_preparation",
        "pure_road_preparation",
    }
    assert by_family["sea_road_preparation"].display_name == "Sea + Road"
    assert by_family["air_road_preparation"].display_name == "Air + Road"
    assert by_family["pure_road_preparation"].display_name == "Pure Road"
    assert by_family["sea_road_preparation"].blockers == []
    assert by_family["air_road_preparation"].blockers == []
    assert by_family["pure_road_preparation"].blockers
    assert not any("SZX" in candidate for candidate in by_family["air_road_preparation"].gateways.destination_candidates)
    for family_id in ("sea_road_preparation", "air_road_preparation", "pure_road_preparation"):
        path = by_family[family_id]
        risk_text = " ".join(risk.message for risk in path.risks)
        action_text = " ".join(path.next_actions)
        assert "cargo_flags.pharma" not in risk_text
        assert "cargo_flags.food_perishable" not in risk_text
        assert "cargo_flags.live_animals" not in risk_text
        assert "commercial.incoterm" not in action_text
        assert "core_shipment.dimensions" not in action_text
    evidence.model_dump(mode="json")
