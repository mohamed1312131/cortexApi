from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from app.schemas.operational_evidence import (
    CostBoundaryEvidence,
    CostEstimate,
    DocumentEvidence,
    EvidenceQuality,
    EvidenceSourceRef,
    EvidenceStatus,
    GatewayEvidence,
    HandlingSafetyEvidence,
    OperationalEvidence,
    OperationalPathEvidence,
    OperationalRiskEvidence,
    RecommendationRole,
    RiskCategory,
    RiskSeverity,
    RouteLegEvidence,
    RouteLegType,
    ScheduleBoundaryEvidence,
    TransitTimeEstimate,
)
from app.schemas.shipment_request import RequestedMode


_DISPLAY_NAMES = {
    "sea_road_preparation": "Sea + Road",
    "air_road_preparation": "Air + Road",
    "pure_road_preparation": "Pure Road",
    "road_preparation": "Pure Road",
    "sea_preparation": "Sea",
    "air_preparation": "Air",
    "rail_multimodal_study": "Rail / Multimodal",
}

_LEG_MODES = {
    "sea_road_preparation": [RequestedMode.road, RequestedMode.sea, RequestedMode.road],
    "air_road_preparation": [RequestedMode.road, RequestedMode.air, RequestedMode.road],
    "pure_road_preparation": [RequestedMode.road],
    "road_preparation": [RequestedMode.road],
    "sea_preparation": [RequestedMode.sea],
    "air_preparation": [RequestedMode.air],
    "rail_multimodal_study": [],
}

_MODE_PATH_FAMILY = {
    RequestedMode.road: "pure_road_preparation",
    RequestedMode.sea: "sea_road_preparation",
    RequestedMode.air: "air_road_preparation",
}

_PATH_MAIN_MODE = {
    "sea_road_preparation": RequestedMode.sea,
    "air_road_preparation": RequestedMode.air,
    "pure_road_preparation": RequestedMode.road,
    "road_preparation": RequestedMode.road,
    "sea_preparation": RequestedMode.sea,
    "air_preparation": RequestedMode.air,
}

_DOCUMENT_BLOCKS = {
    "sea_road_preparation": ("SEA-F", "documents"),
    "air_road_preparation": ("AIR-F", "required_documents"),
    "pure_road_preparation": ("ROAD-F", "documents"),
    "road_preparation": ("ROAD-F", "documents"),
}

_COST_BLOCKS = {
    "sea_road_preparation": "SEA-COST",
    "air_road_preparation": "AIR-COST",
    "pure_road_preparation": "ROAD-COST",
    "road_preparation": "ROAD-COST",
}

_GATEWAY_BLOCKS = {
    "sea_road_preparation": "SEA-C",
    "air_road_preparation": "AIR-C",
}

_SCHEDULE_BLOCKS = {
    "sea_road_preparation": "SEA-I",
    "air_road_preparation": "AIR-I",
    "pure_road_preparation": "ROAD-F",
    "road_preparation": "ROAD-F",
}


def build_operational_evidence(
    *,
    fact_package,
    reasoning_decision=None,
    layer2_summary=None,
) -> OperationalEvidence:
    request = _get(fact_package, "request")
    blocks = list(_get(fact_package, "block_responses", []) or [])
    shipment = _copy_model_or_mapping(request)
    case_id = _text(_get(fact_package, "case_id")) or _text(_get(request, "case_id")) or "unknown"

    ranked_options = list(_get(reasoning_decision, "ranked_readiness_options", []) or [])
    confidence_band = _confidence_band(reasoning_decision)
    non_blocked_ranked_ids = _non_blocked_ranked_option_ids(ranked_options)

    if ranked_options:
        paths = [
            _path_from_ranked_option(
                option,
                request=request,
                blocks=blocks,
                confidence_band=confidence_band,
                layer2_summary=layer2_summary,
                non_blocked_ranked_ids=non_blocked_ranked_ids,
            )
            for option in ranked_options
        ]
    else:
        paths = _paths_from_modes_covered(
            fact_package=fact_package,
            request=request,
            blocks=blocks,
            layer2_summary=layer2_summary,
        )

    return OperationalEvidence(
        case_id=case_id,
        generated_from={
            "fact_package_case_id": _text(_get(fact_package, "case_id")),
            "reasoning_decision_id": _text(_get(reasoning_decision, "reasoning_decision_id")),
        },
        shipment=shipment,
        paths=paths,
        global_blockers=_global_blockers(fact_package),
        global_unknowns=_global_unknowns(fact_package, reasoning_decision),
        global_limitations=_global_limitations(fact_package),
    )


def _path_from_ranked_option(
    option: Any,
    *,
    request: Any,
    blocks: list[Any],
    confidence_band: str | None,
    layer2_summary: Any,
    non_blocked_ranked_ids: list[int],
) -> OperationalPathEvidence:
    path_family_id = _text(_get(option, "path_family_id")) or _text(_get(option, "path_family")) or "unknown"
    rank = _get(option, "rank")
    mode = _coerce_mode(_get(option, "mode")) or _primary_mode_from_family(path_family_id)
    readiness_band = _text(_value(_get(option, "readiness_band")))
    status_text = _text(_get(option, "status"))
    is_blocked = _is_blocked(readiness_band, status_text)
    is_specialized = _is_specialized_study(readiness_band, status_text)
    role = _recommendation_role(
        option_id=id(option),
        is_blocked=is_blocked,
        is_specialized=is_specialized,
        non_blocked_ranked_ids=non_blocked_ranked_ids,
    )

    gateways = _gateway_evidence(path_family_id, blocks)

    return OperationalPathEvidence(
        path_family_id=path_family_id,
        rank=rank if isinstance(rank, int) else None,
        primary_mode=mode,
        leg_modes=_safe_leg_modes(path_family_id),
        display_name=_display_name(path_family_id),
        recommendation_role=role,
        status=_path_status(has_evidence=True, is_blocked=is_blocked),
        readiness_band=readiness_band,
        confidence_band=confidence_band,
        evidence_quality=_evidence_quality(readiness_band),
        route_legs=_route_legs(path_family_id, request, gateways),
        gateways=gateways,
        cost=_cost_evidence(path_family_id=path_family_id, mode=mode, blocks=blocks, layer2_summary=layer2_summary),
        schedule=_schedule_evidence(path_family_id, request, blocks),
        documents=_document_evidence(path_family_id, blocks, request=request),
        handling_safety=_handling_safety_evidence(path_family_id, blocks),
        blockers=_blockers_from_hard_gates(_get(option, "hard_gates", []) or []),
        risks=_risks_from_unknowns(_get(option, "unknowns", []) or [], request=request),
        missing_inputs=_missing_inputs_from_option(option, request=request),
        next_actions=_friendly_next_actions(_get(option, "next_actions", []) or [], request=request),
    )


def _paths_from_modes_covered(
    *,
    fact_package: Any,
    request: Any,
    blocks: list[Any],
    layer2_summary: Any,
) -> list[OperationalPathEvidence]:
    modes = [
        mode
        for mode in (_coerce_mode(item) for item in _get(_get(fact_package, "derived_rollup"), "modes_covered", []) or [])
        if mode is not None and mode is not RequestedMode.unknown
    ]
    paths: list[OperationalPathEvidence] = []
    for mode in modes:
        path_family_id = _MODE_PATH_FAMILY.get(mode, f"{mode.value}_preparation")
        gateways = _gateway_evidence(path_family_id, blocks)
        paths.append(
            OperationalPathEvidence(
                path_family_id=path_family_id,
                primary_mode=mode,
                leg_modes=_safe_leg_modes(path_family_id),
                display_name=_display_name(path_family_id),
                status=EvidenceStatus.unknown,
                route_legs=_route_legs(path_family_id, request, gateways),
                gateways=gateways,
                cost=_cost_evidence(path_family_id=path_family_id, mode=mode, blocks=blocks, layer2_summary=layer2_summary),
                schedule=_schedule_evidence(path_family_id, request, blocks),
                documents=_document_evidence(path_family_id, blocks, request=request),
                handling_safety=_handling_safety_evidence(path_family_id, blocks),
                missing_inputs=_filter_missing_inputs(
                    list(_get(_get(fact_package, "derived_rollup"), "missing_fields", []) or []),
                    request=request,
                ),
            )
        )
    return paths


def _display_name(path_family_id: str) -> str:
    if path_family_id in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[path_family_id]
    return path_family_id.replace("_", " ").title()


def _safe_leg_modes(path_family_id: str) -> list[RequestedMode]:
    return list(_LEG_MODES.get(path_family_id, []))


def _primary_mode_from_family(path_family_id: str) -> RequestedMode:
    if path_family_id in _PATH_MAIN_MODE:
        return _PATH_MAIN_MODE[path_family_id]
    modes = _safe_leg_modes(path_family_id)
    if RequestedMode.sea in modes:
        return RequestedMode.sea
    if RequestedMode.air in modes:
        return RequestedMode.air
    if RequestedMode.road in modes:
        return RequestedMode.road
    return RequestedMode.unknown


def _route_legs(
    path_family_id: str,
    request: Any,
    gateways: GatewayEvidence | None = None,
) -> list[RouteLegEvidence]:
    leg_modes = _safe_leg_modes(path_family_id)
    if not leg_modes:
        return []

    origin = _lane_value(request, "origin_raw") or _lane_value(request, "origin_city")
    destination = _lane_value(request, "destination_raw") or _lane_value(request, "destination_city")
    if len(leg_modes) == 1:
        return [
            RouteLegEvidence(
                leg_type=RouteLegType.main_leg,
                mode=leg_modes[0],
                origin=origin,
                destination=destination,
            )
        ]
    origin_gateway = _first_or_none(_get(gateways, "origin_candidates", []) or [])
    destination_gateway = _first_or_none(_get(gateways, "destination_candidates", []) or [])
    return [
        RouteLegEvidence(
            leg_type=RouteLegType.first_mile,
            mode=leg_modes[0],
            origin=origin,
            destination=origin_gateway,
            status=EvidenceStatus.requires_validation,
            requires_validation=[_gateway_validation_text(path_family_id, "origin")],
        ),
        RouteLegEvidence(
            leg_type=RouteLegType.main_leg,
            mode=leg_modes[1],
            origin=origin_gateway,
            destination=destination_gateway,
            status=EvidenceStatus.requires_validation,
            requires_validation=[_gateway_validation_text(path_family_id, "gateway")],
        ),
        RouteLegEvidence(
            leg_type=RouteLegType.last_mile,
            mode=leg_modes[2],
            origin=destination_gateway,
            destination=destination,
            status=EvidenceStatus.requires_validation,
            requires_validation=[_gateway_validation_text(path_family_id, "destination")],
        ),
    ]


def _recommendation_role(
    *,
    option_id: int,
    is_blocked: bool,
    is_specialized: bool,
    non_blocked_ranked_ids: list[int],
) -> RecommendationRole:
    if is_blocked:
        return RecommendationRole.blocked
    if is_specialized:
        return RecommendationRole.specialized_study
    if non_blocked_ranked_ids[:1] == [option_id]:
        return RecommendationRole.recommended
    if len(non_blocked_ranked_ids) > 1 and non_blocked_ranked_ids[1] == option_id:
        return RecommendationRole.fallback
    return RecommendationRole.unknown


def _non_blocked_ranked_option_ids(ranked_options: list[Any]) -> list[int]:
    sorted_options = sorted(
        ranked_options,
        key=lambda option: _get(option, "rank") if isinstance(_get(option, "rank"), int) else 10_000,
    )
    return [
        id(option)
        for option in sorted_options
        if not _is_blocked(_text(_value(_get(option, "readiness_band"))), _text(_get(option, "status")))
        and not _is_specialized_study(_text(_value(_get(option, "readiness_band"))), _text(_get(option, "status")))
    ]


def _path_status(*, has_evidence: bool, is_blocked: bool) -> EvidenceStatus:
    if is_blocked:
        return EvidenceStatus.blocked
    if has_evidence:
        return EvidenceStatus.requires_validation
    return EvidenceStatus.unknown


def _evidence_quality(readiness_band: str | None) -> EvidenceQuality:
    text = (readiness_band or "").lower()
    if text in {"high", "medium"}:
        return EvidenceQuality.partial
    if text in {"low", "medium_low"}:
        return EvidenceQuality.low_data
    if "blocked" in text:
        return EvidenceQuality.not_available
    return EvidenceQuality.unknown


def _blockers_from_hard_gates(hard_gates: list[Any]) -> list[OperationalRiskEvidence]:
    blockers = []
    for gate in hard_gates:
        blockers.append(
            OperationalRiskEvidence(
                category=RiskCategory.compliance,
                severity=_risk_severity(_get(gate, "severity")),
                message=_text(_get(gate, "message")) or _text(_get(gate, "gate_id")) or "Hard gate present",
                mitigation=_text(_get(gate, "basis")),
                source_refs=[_source_ref_from_gate(gate)],
            )
        )
    return blockers


def _risks_from_unknowns(unknowns: list[Any], *, request: Any) -> list[OperationalRiskEvidence]:
    risks = []
    seen_fields: set[str] = set()
    for unknown in unknowns:
        field = _text(_get(unknown, "field"))
        if _should_hide_unknown_field(field, request):
            continue
        if field and field in seen_fields:
            continue
        if field:
            seen_fields.add(field)
        reason = _text(_get(unknown, "reason"))
        impact = _text(_get(unknown, "impact"))
        message = _risk_message(field, reason)
        risks.append(
            OperationalRiskEvidence(
                category=RiskCategory.data_gap,
                severity=RiskSeverity.unknown,
                message=message,
                mitigation=impact,
            )
        )
    return risks


def _missing_inputs_from_option(option: Any, *, request: Any) -> list[str]:
    fields = []
    for unknown in _get(option, "unknowns", []) or []:
        field = _text(_get(unknown, "field"))
        if field:
            fields.append(field)
    for field in _get(option, "missing_fields", []) or []:
        if _text(field):
            fields.append(_text(field))
    return _filter_missing_inputs(fields, request=request)


_IRRELEVANT_GENERAL_CARGO_FIELDS = {
    "cargo_flags.pharma",
    "cargo_flags.food_perishable",
    "cargo_flags.live_animals",
}

_FRIENDLY_FIELD_LABELS = {
    "commercial.incoterm": "Incoterm",
    "incoterm": "Incoterm",
    "core_shipment.dimensions": "pallet dimensions",
    "dimensions": "pallet dimensions",
    "cargo.hs_code": "HS code or commodity classification",
    "hs_code": "HS code or commodity classification",
    "commercial.cargo_value": "cargo value",
    "cargo_value": "cargo value",
}

_FRIENDLY_ACTIONS = {
    "commercial.incoterm": "Confirm Incoterm.",
    "incoterm": "Confirm Incoterm.",
    "core_shipment.dimensions": "Confirm pallet dimensions.",
    "dimensions": "Confirm pallet dimensions.",
    "cargo.hs_code": "Confirm HS code or commodity classification.",
    "hs_code": "Confirm HS code or commodity classification.",
    "schedule.deadline": "Validate schedule against the requested delivery deadline.",
    "deadline": "Validate schedule against the requested delivery deadline.",
    "cost.live_quote": "Request a live forwarder quote.",
    "live carrier quote": "Request a live forwarder quote.",
    "live freight quote": "Request a live forwarder quote.",
    "live road quote": "Request a live forwarder quote.",
    "commercial.cargo_value": "Confirm cargo value if available.",
    "cargo_value": "Confirm cargo value if available.",
}

_PROFILE_DOCUMENT_KEYWORDS = {
    "human_remains": (
        "human remains",
        "death certificate",
        "embalming",
        "coffin",
        "sealing certificate",
        "consular clearance",
    ),
    "live_animals": (
        "live animal",
        "live animals",
        "animal health",
        "veterinary",
        "vaccination",
        "pet",
        "species",
    ),
    "pharma": (
        "pharma",
        "medicine",
        "medical product",
        "medicine/controlled",
        "health product",
        "temperature documents",
        "cold chain",
        "certificate/coa",
    ),
    "food_perishable": (
        "perishable",
        "food",
        "health certificate",
        "phytosanitary",
        "ched",
        "halal",
        "slaughter",
    ),
}


def _should_hide_unknown_field(field: str | None, request: Any) -> bool:
    if field not in _IRRELEVANT_GENERAL_CARGO_FIELDS:
        return False
    active_profiles = {_text(profile) for profile in _get(request, "active_profiles", []) or []}
    if not active_profiles or "general_cargo" not in active_profiles:
        return False
    relevant_profile = field.rsplit(".", 1)[-1]
    if relevant_profile in active_profiles:
        return False
    flag_value = (_text(_get(_get(request, "cargo_flags"), relevant_profile)) or "").lower()
    return flag_value in {"", "no", "unknown"}


def _risk_message(field: str | None, reason: str | None) -> str:
    label = _FRIENDLY_FIELD_LABELS.get(field or "", _friendly_field_label(field))
    if label and reason:
        return f"Evidence gap for {label}: {reason}."
    if label:
        return f"Evidence gap for {label}."
    return reason or "Evidence gap requires validation."


def _filter_missing_inputs(fields: list[str], *, request: Any) -> list[str]:
    return _dedupe(
        [
            field
            for field in fields
            if field and not _should_hide_unknown_field(field, request)
        ]
    )


def _friendly_next_actions(actions: list[Any], *, request: Any) -> list[str]:
    friendly = []
    for action in actions:
        text = _text(action)
        if not text or _should_hide_unknown_field(text, request):
            continue
        friendly.append(_friendly_action(text))
    return _dedupe(friendly)


def _friendly_action(text: str) -> str:
    mapped = _FRIENDLY_ACTIONS.get(text)
    if mapped:
        return mapped
    lowered = text.lower()
    for key, action in _FRIENDLY_ACTIONS.items():
        if key in lowered:
            return action
    if lowered.startswith("validate "):
        return text if text.endswith(".") else f"{text}."
    label = _friendly_field_label(text)
    return f"Confirm {label}." if label else text


def _friendly_field_label(field: str | None) -> str | None:
    if not field:
        return None
    label = field.replace("_", " ").replace(".", " ").strip()
    return label or None


def _profile_relevant_documents(docs: list[str], *, request: Any) -> list[str]:
    return _dedupe(
        [
            doc
            for doc in docs
            if doc and _is_profile_relevant_document(doc, request=request)
        ]
    )


def _is_profile_relevant_document(doc: str, *, request: Any) -> bool:
    lowered = doc.lower()
    for profile, keywords in _PROFILE_DOCUMENT_KEYWORDS.items():
        if not any(keyword in lowered for keyword in keywords):
            continue
        if _profile_is_relevant(profile, request=request):
            return True
        return False
    return True


def _profile_is_relevant(profile: str, *, request: Any) -> bool:
    active_profiles = {_text(item) for item in _get(request, "active_profiles", []) or []}
    if profile == "human_remains":
        return "human_remains" in active_profiles
    if profile in active_profiles:
        return True
    flag_value = (_text(_get(_get(request, "cargo_flags"), profile)) or "").lower()
    return flag_value in {"yes", "likely"}


def _document_evidence(path_family_id: str, blocks: list[Any], *, request: Any) -> DocumentEvidence:
    block_id, field = _DOCUMENT_BLOCKS.get(path_family_id, (None, None))
    block = _block_by_id(blocks, block_id)
    docs = _profile_relevant_documents(
        _string_list(_get(_block_data(block), field)) if field else [],
        request=request,
    )
    if docs:
        return DocumentEvidence(
            status=EvidenceStatus.available,
            required_documents=docs,
            source_refs=[_source_ref_from_block(block, field_path=f"data.{field}")],
        )

    limitation = (
        f"{block_id} did not provide document evidence."
        if block_id
        else f"No document evidence mapping is defined for {path_family_id}."
    )
    return DocumentEvidence(
        status=EvidenceStatus.not_available if block is not None else EvidenceStatus.unknown,
        limitations=[limitation],
        source_refs=[_source_ref_from_block(block, field_path=f"data.{field}")] if block is not None and field else [],
    )


def _cost_evidence(
    *,
    path_family_id: str,
    mode: RequestedMode,
    blocks: list[Any],
    layer2_summary: Any,
) -> CostBoundaryEvidence:
    block = _block_by_id(blocks, _COST_BLOCKS.get(path_family_id))
    data = _block_data(block)
    if block is None:
        return _cost_placeholder(mode=mode, layer2_summary=layer2_summary)

    if path_family_id == "air_road_preparation":
        raw_estimate = _get(data, "estimated_cost_usd")
        estimate = _cost_estimate(raw_estimate)
        limitations = []
        if estimate is None:
            limitations.append("AIR-COST did not provide normalized low/typical/high values.")
        transit_days = _get(data, "transit_days_door_to_door")
        if transit_days is not None:
            limitations.append(f"Transit reference from AIR-COST: {transit_days}.")
        return CostBoundaryEvidence(
            status=EvidenceStatus.planning_reference if raw_estimate is not None else EvidenceStatus.unknown,
            currency=_text(_get(data, "currency")) or _text(_get(data, "estimate_currency")) or ("USD" if raw_estimate is not None else None),
            estimate=estimate,
            basis=_text(_get(data, "rate_basis")) or _text(_get(data, "cost_basis")) or _text(_get(data, "cost_status")),
            limitations=limitations,
            missing_inputs=[] if estimate is not None else ["live carrier quote"],
            source_refs=[_source_ref_from_block(block, field_path="data.estimated_cost_usd")],
        )

    if path_family_id == "sea_road_preparation":
        evidence_keys = [
            key
            for key in ("lane_benchmark_examples", "surcharge_examples", "local_charge_examples")
            if _get(data, key)
        ]
        return CostBoundaryEvidence(
            status=EvidenceStatus.planning_reference if evidence_keys else EvidenceStatus.unknown,
            basis=_examples_basis("SEA-COST", evidence_keys),
            limitations=[
                "SEA-COST provides planning-reference examples only; no normalized low/typical/high estimate was derived."
            ],
            missing_inputs=["live freight quote"] if evidence_keys else ["sea cost benchmark evidence"],
            source_refs=[_source_ref_from_block(block, field_path="data")],
        )

    if path_family_id in {"pure_road_preparation", "road_preparation"}:
        evidence_keys = ["cost_reference_examples"] if _get(data, "cost_reference_examples") else []
        return CostBoundaryEvidence(
            status=EvidenceStatus.planning_reference if evidence_keys else EvidenceStatus.unknown,
            basis=_examples_basis("ROAD-COST", evidence_keys),
            limitations=[
                "ROAD-COST provides planning-reference examples only; no normalized low/typical/high estimate was derived."
            ],
            missing_inputs=["live road quote"] if evidence_keys else ["road cost reference evidence"],
            source_refs=[_source_ref_from_block(block, field_path="data")],
        )

    return _cost_placeholder(mode=mode, layer2_summary=layer2_summary)


def _schedule_evidence(path_family_id: str, request: Any, blocks: list[Any]) -> ScheduleBoundaryEvidence:
    schedule = _schedule_placeholder(request)
    block = _block_by_id(blocks, _SCHEDULE_BLOCKS.get(path_family_id))
    data = _block_data(block)
    limitations: list[str] = []
    feasibility_statement = None
    transit_time = None
    status = EvidenceStatus.requires_validation

    if path_family_id == "air_road_preparation":
        transit_time = _transit_time_estimate(_get(_block_data(_block_by_id(blocks, "AIR-COST")), "transit_days_door_to_door"))
        route_status = _text(_get(data, "route_status"))
        if route_status:
            feasibility_statement = f"AIR-I route status: {route_status}."
            status = EvidenceStatus.planning_reference
        else:
            limitations.append("AIR-I did not provide route status evidence.")
    elif path_family_id == "sea_road_preparation":
        schedule_status = _text(_get(data, "schedule_status"))
        if schedule_status:
            feasibility_statement = f"SEA-I schedule status: {schedule_status}."
            status = EvidenceStatus.planning_reference
        else:
            limitations.append("SEA-I did not provide schedule status evidence.")
    elif path_family_id in {"pure_road_preparation", "road_preparation"}:
        if _path_has_triggered_blocking_gate(path_family_id, blocks):
            feasibility_statement = "Road timing is not evaluated because the pure road path is blocked."
            limitations = [
                "Pure road is blocked by corridor feasibility evidence.",
                "Road timing is not evaluated because the pure road path is blocked.",
            ]
            status = EvidenceStatus.blocked
        else:
            references = _readable_road_schedule_items(_get(data, "realistic_transit_reference"))
            buffers = _readable_road_schedule_items(_get(data, "border_buffer_examples"))
            if references or buffers:
                feasibility_statement = "ROAD-F provides road transit or border-buffer planning references."
                limitations.extend(references + buffers)
                status = EvidenceStatus.planning_reference
            else:
                limitations.append("ROAD-F did not provide road transit reference evidence.")

        if status is EvidenceStatus.planning_reference:
            feasibility_statement = "ROAD-F provides road transit or border-buffer planning references."

    schedule.status = status
    schedule.transit_time = transit_time
    schedule.feasibility_statement = feasibility_statement or "Schedule requires validation against live carrier and route availability."
    schedule.requires_live_schedule = True
    schedule.limitations = limitations
    schedule.missing_inputs = ["live schedule"] if status is not EvidenceStatus.planning_reference else []
    schedule.source_refs = [_source_ref_from_block(block, field_path="data")] if block is not None else []
    return schedule


def _gateway_evidence(path_family_id: str, blocks: list[Any]) -> GatewayEvidence:
    block = _block_by_id(blocks, _GATEWAY_BLOCKS.get(path_family_id))
    data = _block_data(block)
    if path_family_id == "sea_road_preparation":
        candidates = _dedupe(
            [
                item
                for item in [
                    _gateway_label(_get(data, "main_port_name"), _get(data, "unlocode")),
                    _gateway_label(_get(data, "alternate_port_name"), None),
                ]
                if item
            ]
        )
        missing_message = (
            "Validate export/import sea gateway candidates."
            if candidates
            else "Export sea gateway could not be resolved from current local evidence."
        )
        return _gateway_result(
            block=block,
            origin_candidates=candidates,
            destination_candidates=[],
            missing_message=missing_message,
            field_path="data.main_port_name",
        )

    if path_family_id == "air_road_preparation":
        origin_candidates = _dedupe(
            [
                item
                for item in [
                    _gateway_label(_get(data, "airport_name"), _get(data, "airport_code")),
                    _text(_get(data, "airport_code")),
                ]
                if item
            ]
        )
        evidence = _gateway_result(
            block=block,
            origin_candidates=origin_candidates,
            destination_candidates=[],
            missing_message="Destination airport candidate requires validation.",
            field_path="data.airport_code",
        )
        handlers = _text(_get(data, "known_handlers"))
        if handlers:
            evidence.assumptions.append(f"Known handlers noted by AIR-C: {handlers}")
        return evidence

    return GatewayEvidence(
        status=EvidenceStatus.unknown,
        requires_validation=["Confirm first-mile and last-mile gateways if the path is pursued."],
    )


def _gateway_result(
    *,
    block: Any,
    origin_candidates: list[str],
    destination_candidates: list[str],
    missing_message: str,
    field_path: str,
) -> GatewayEvidence:
    if origin_candidates or destination_candidates:
        return GatewayEvidence(
            status=EvidenceStatus.requires_validation,
            origin_candidates=list(origin_candidates),
            destination_candidates=list(destination_candidates),
            requires_validation=[missing_message],
            source_refs=[_source_ref_from_block(block, field_path=field_path)],
        )
    return GatewayEvidence(
        status=EvidenceStatus.unknown if block is None else EvidenceStatus.not_available,
        requires_validation=[missing_message],
        source_refs=[_source_ref_from_block(block, field_path=field_path)] if block is not None else [],
    )


def _handling_safety_evidence(path_family_id: str, blocks: list[Any]) -> HandlingSafetyEvidence:
    main_mode = _PATH_MAIN_MODE.get(path_family_id)
    relevant = [block for block in blocks if _coerce_mode(_get(block, "mode")) is main_mode]
    planning_factors = _dedupe(
        [
            item
            for block in relevant
            for item in _string_list(_get(block, "planning_factors"))
        ]
    )
    return HandlingSafetyEvidence(
        status=EvidenceStatus.planning_reference if planning_factors else EvidenceStatus.unknown,
        requirements=planning_factors,
        cargo_fit_notes=planning_factors,
        source_refs=[_source_ref_from_block(block, field_path="planning_factors") for block in relevant if _get(block, "planning_factors")],
    )


def _cost_placeholder(*, mode: RequestedMode, layer2_summary: Any) -> CostBoundaryEvidence:
    summary = _matching_cost_summary(mode, layer2_summary)
    if summary is None:
        return CostBoundaryEvidence()

    estimate = _cost_estimate(_get(summary, "estimate"))
    return CostBoundaryEvidence(
        status=EvidenceStatus.planning_reference,
        currency=_text(_get(summary, "currency")),
        estimate=estimate,
        basis=_text(_get(summary, "basis")),
        source_refs=[
            EvidenceSourceRef(
                block_id=_text(_get(summary, "block_id")),
                mode=mode,
                field_path="layer2_summary.cost_summaries",
            )
        ],
    )


def _matching_cost_summary(mode: RequestedMode, layer2_summary: Any) -> Any | None:
    for summary in _get(layer2_summary, "cost_summaries", []) or []:
        if _coerce_mode(_get(summary, "mode")) is mode:
            return summary
    return None


def _cost_estimate(raw: Any) -> CostEstimate | None:
    if not raw:
        return None
    if isinstance(raw, int | float):
        return CostEstimate(typical=float(raw))
    return CostEstimate(
        low=_number(_get(raw, "low")),
        typical=_number(_get(raw, "typical")) or _number(_get(raw, "median")) or _number(_get(raw, "amount")),
        high=_number(_get(raw, "high")),
    )


def _schedule_placeholder(request: Any) -> ScheduleBoundaryEvidence:
    commercial = _get(request, "commercial")
    return ScheduleBoundaryEvidence(
        ready_date=_text(_get(commercial, "ready_date")) or _text(_get(request, "ready_date")),
        deadline=_text(_get(commercial, "deadline")) or _text(_get(request, "deadline")),
    )


def _path_has_triggered_blocking_gate(path_family_id: str, blocks: list[Any]) -> bool:
    main_mode = _PATH_MAIN_MODE.get(path_family_id)
    for block in blocks:
        block_mode = _coerce_mode(_get(block, "mode"))
        if main_mode is not None and block_mode is not None and block_mode is not main_mode:
            continue
        for gate in _get(block, "hard_gates", []) or []:
            gate_mode = _coerce_mode(_get(gate, "mode"))
            if main_mode is not None and gate_mode is not None and gate_mode is not main_mode:
                continue
            status = (_text(_value(_get(gate, "status"))) or "").lower()
            severity = (_text(_value(_get(gate, "severity"))) or "").lower()
            if status == "triggered" and severity == "blocking":
                return True
    return False


def _readable_road_schedule_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list | tuple | set):
        out: list[str] = []
        for item in value:
            out.extend(_readable_road_schedule_items(item))
        return _dedupe(out)
    if isinstance(value, dict):
        readable_keys = (
            "message",
            "note",
            "description",
            "basis",
            "planning_note",
            "label",
            "summary",
            "value",
            "reference",
        )
        out = [_text(_get(value, key)) for key in readable_keys if _text(_get(value, key))]
        return _dedupe([item for item in out if item])
    text = _text(value)
    return [text] if text else []


def _global_blockers(fact_package: Any) -> list[OperationalRiskEvidence]:
    gates = list(_get(fact_package, "global_hard_gates", []) or [])
    return _blockers_from_hard_gates(
        [gate for gate in gates if not _is_mode_specific_gate(gate)]
    )


def _global_unknowns(fact_package: Any, reasoning_decision: Any) -> list[str]:
    unknowns = []
    for source in [
        _get(fact_package, "global_unknowns", []) or [],
        _get(reasoning_decision, "global_unknowns", []) or [],
    ]:
        for item in source:
            message = ": ".join(
                part
                for part in [_text(_get(item, "field")), _text(_get(item, "reason"))]
                if part
            )
            if message:
                unknowns.append(message)
    return _dedupe(unknowns)


def _global_limitations(fact_package: Any) -> list[str]:
    limitations = []
    completeness = _get(fact_package, "completeness")
    has_path_scoped_road_blocker = _has_path_scoped_road_blocker_with_alternatives(fact_package)
    added_path_scoped_note = False
    for reason in _get(completeness, "reasons", []) or []:
        reason_text = _text(reason)
        if (
            has_path_scoped_road_blocker
            and reason_text
            and "blocking hard gate" in reason_text.lower()
        ):
            if not added_path_scoped_note:
                limitations.append(
                    "The case contains a blocked pure-road path, but other paths remain evaluable."
                )
                limitations.append(
                    "Pure Road is blocked. This road blocker does not apply to Sea + Road or Air + Road."
                )
                added_path_scoped_note = True
            continue
        limitations.append(reason_text)
    limitations.extend(_get(fact_package, "global_missing_fields", []) or [])
    return _dedupe([_text(item) for item in limitations if _text(item)])


def _source_ref_from_gate(gate: Any) -> EvidenceSourceRef:
    return EvidenceSourceRef(
        block_id=_text(_get(gate, "source_block")),
        mode=_coerce_mode(_get(gate, "mode")),
        field_path=_text(_get(gate, "gate_id")),
    )


def _source_ref_from_block(block: Any, *, field_path: str | None = None) -> EvidenceSourceRef:
    provenance = _get(block, "provenance")
    return EvidenceSourceRef(
        block_id=_text(_get(block, "block_id")),
        mode=_coerce_mode(_get(block, "mode")),
        field_path=field_path,
        record_id=_text(_get(provenance, "record_id")),
        source=_text(_get(provenance, "source")),
        provider_used=_text(_get(_get(provenance, "provider_used"), "value")) or _text(_get(provenance, "provider_used")),
    )


def _is_mode_specific_gate(gate: Any) -> bool:
    mode = _coerce_mode(_get(gate, "mode"))
    return mode is not None and mode is not RequestedMode.unknown


def _has_path_scoped_road_blocker_with_alternatives(fact_package: Any) -> bool:
    modes = {
        mode
        for mode in (
            _coerce_mode(item)
            for item in _get(_get(fact_package, "derived_rollup"), "modes_covered", []) or []
        )
        if mode is not None and mode is not RequestedMode.unknown
    }
    has_non_road_mode = bool(modes & {RequestedMode.sea, RequestedMode.air})
    if not has_non_road_mode:
        return False
    for gate in _get(fact_package, "global_hard_gates", []) or []:
        if _coerce_mode(_get(gate, "mode")) is not RequestedMode.road:
            continue
        if (_text(_value(_get(gate, "status"))) or "").lower() != "triggered":
            continue
        if (_text(_value(_get(gate, "severity"))) or "").lower() == "blocking":
            return True
    return False


def _block_by_id(blocks: list[Any], block_id: str | None) -> Any | None:
    if not block_id:
        return None
    for block in blocks:
        if _text(_get(block, "block_id")) == block_id:
            return block
    return None


def _block_data(block: Any) -> dict[str, Any]:
    data = _get(block, "data", {})
    return data if isinstance(data, dict) else {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [_text(key) for key, enabled in value.items() if enabled and _text(key)]
    if isinstance(value, list | tuple | set):
        out = []
        for item in value:
            if isinstance(item, dict):
                text = (
                    _text(_get(item, "name"))
                    or _text(_get(item, "document"))
                    or _text(_get(item, "label"))
                    or _text(_get(item, "description"))
                    or _text(_get(item, "route"))
                    or _text(_get(item, "example"))
                )
            else:
                text = _text(item)
            if text:
                out.append(text)
        return _dedupe(out)
    text = _text(value)
    return [text] if text else []


def _examples_basis(block_id: str, keys: list[str]) -> str | None:
    if not keys:
        return None
    return f"{block_id} planning-reference examples: {', '.join(keys)}."


def _transit_time_estimate(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return TransitTimeEstimate(typical_days=float(raw))
    return TransitTimeEstimate(
        low_days=_number(_get(raw, "low_days")) or _number(_get(raw, "low")),
        typical_days=_number(_get(raw, "typical_days")) or _number(_get(raw, "typical")) or _number(_get(raw, "median")),
        high_days=_number(_get(raw, "high_days")) or _number(_get(raw, "high")),
    )


def _gateway_label(name: Any, code: Any) -> str | None:
    name_text = _text(name)
    code_text = _text(code)
    if name_text and code_text:
        return f"{name_text} ({code_text})"
    return name_text or code_text


def _first_or_none(values: list[str]) -> str | None:
    return values[0] if values else None


def _gateway_validation_text(path_family_id: str, position: str) -> str:
    if path_family_id == "sea_road_preparation":
        return f"Validate {position} sea gateway before execution."
    if path_family_id == "air_road_preparation":
        return f"Validate {position} airport before execution."
    return f"Validate {position} route point before execution."


def _risk_severity(value: Any) -> RiskSeverity:
    text = (_text(_value(value)) or "").lower()
    if text in {item.value for item in RiskSeverity}:
        return RiskSeverity(text)
    return RiskSeverity.unknown


def _confidence_band(reasoning_decision: Any) -> str | None:
    confidence = _get(reasoning_decision, "confidence")
    return _text(_value(_get(confidence, "band")))


def _lane_value(request: Any, field: str) -> str | None:
    return _text(_get(_get(request, "lane"), field))


def _is_blocked(readiness_band: str | None, status: str | None) -> bool:
    return "blocked" in f"{readiness_band or ''} {status or ''}".lower()


def _is_specialized_study(readiness_band: str | None, status: str | None) -> bool:
    text = f"{readiness_band or ''} {status or ''}".lower()
    return "specialized" in text or "study" in text


def _coerce_mode(value: Any) -> RequestedMode | None:
    value = _value(value)
    if isinstance(value, RequestedMode):
        return value
    if isinstance(value, str):
        try:
            return RequestedMode(value)
        except ValueError:
            return None
    return None


def _copy_model_or_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return deepcopy(value)
    return deepcopy(getattr(value, "__dict__", {}))


def _get(obj: Any, field: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _text(value: Any) -> str | None:
    value = _value(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out
