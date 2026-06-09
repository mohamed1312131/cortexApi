from __future__ import annotations

from app.schemas import (
    EmptyResponseBehavior,
    FallbackPolicy,
    FetchPlan,
    FetchPlanItem,
    FetchPriority,
    FlagState,
    RequestedMode,
    RequiredInput,
    ValidatedShipmentRequest,
)


def _road_c_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="ROAD-C",
        mode=RequestedMode.road,
        reason="road corridor viability must be checked before road preparation",
        priority=FetchPriority.fail_fast,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="ROAD-C needs origin ISO country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="ROAD-C needs destination ISO country",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.fail_fast_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _road_a_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="ROAD-A",
        mode=RequestedMode.road,
        reason="road dangerous goods / ADR intake must be checked",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="cargo_flags.dangerous_goods",
                reason="ROAD-A needs dangerous goods flag",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.hard_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _road_b_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="ROAD-B",
        mode=RequestedMode.road,
        reason="road vehicle/load fit must be checked",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="core_shipment.weight_kg",
                reason="ROAD-B needs shipment weight",
            ),
            RequiredInput(
                field="core_shipment.volume_cbm",
                reason="ROAD-B uses shipment volume for vehicle planning",
            ),
            RequiredInput(
                field="core_shipment.dimensions",
                reason="ROAD-B needs shipment dimensions",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _road_f_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="ROAD-F",
        mode=RequestedMode.road,
        reason="road documents and transit planning factors should be surfaced",
        priority=FetchPriority.optional,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="ROAD-F needs origin ISO country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="ROAD-F needs destination ISO country",
            ),
            RequiredInput(
                field="commercial.incoterm",
                reason="ROAD-F uses incoterm for document responsibility planning",
            ),
            RequiredInput(
                field="commercial.ready_date",
                reason="ROAD-F needs ready date for road preparation timing",
            ),
            RequiredInput(
                field="commercial.deadline",
                reason="ROAD-F needs deadline for transit urgency planning",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.planning_unknown,
        fallback_policy=FallbackPolicy.return_planning_only,
    )


def _road_cost_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="ROAD-COST",
        mode=RequestedMode.road,
        reason="road planning cost reference should be checked after readiness facts",
        priority=FetchPriority.optional,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="ROAD-COST needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="ROAD-COST needs destination country",
            ),
            RequiredInput(
                field="core_shipment.weight_kg",
                reason="ROAD-COST uses weight for planning context",
            ),
            RequiredInput(
                field="core_shipment.volume_cbm",
                reason="ROAD-COST uses volume for planning context",
            ),
            RequiredInput(
                field="commercial.incoterm",
                reason="ROAD-COST uses incoterm for cost responsibility context",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.planning_unknown,
        fallback_policy=FallbackPolicy.return_planning_only,
    )


def _road_items() -> list[FetchPlanItem]:
    return [
        _road_c_item(),
        _road_a_item(),
        _road_b_item(),
        _road_f_item(),
        _road_cost_item(),
    ]


def _sea_c_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-C",
        mode=RequestedMode.sea,
        reason="sea port capability must be checked before sea preparation",
        priority=FetchPriority.fail_fast,
        required_inputs=[
            RequiredInput(
                field="lane.origin_city",
                reason="SEA-C needs origin port/city",
            ),
            RequiredInput(
                field="lane.origin_country",
                reason="SEA-C needs origin country",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.fail_fast_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _sea_a_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-A",
        mode=RequestedMode.sea,
        reason="sea dangerous goods acceptance must be checked before sea preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="cargo_flags.dangerous_goods",
                reason="SEA-A needs DG flag for IMDG/DG acceptance planning",
            ),
            RequiredInput(
                field="profiles.dangerous_goods.un_number",
                reason="SEA-A needs UN number for DG acceptance lookup",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.hard_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _sea_d_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-D",
        mode=RequestedMode.sea,
        reason=(
            "sea carrier and trade lane reference must be checked before sea "
            "preparation"
        ),
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="SEA-D needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="SEA-D needs destination country",
            ),
            RequiredInput(
                field="cargo_flags",
                reason=(
                    "SEA-D uses cargo flags for carrier/trade lane planning"
                ),
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _sea_b_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-B",
        mode=RequestedMode.sea,
        reason="container fit must be checked for sea preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="core_shipment.weight_kg",
                reason="SEA-B needs shipment weight",
            ),
            RequiredInput(
                field="core_shipment.volume_cbm",
                reason="SEA-B needs volume for container planning",
            ),
            RequiredInput(
                field="core_shipment.dimensions",
                reason="SEA-B needs dimensions for oversize/container fit",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _sea_f_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-F",
        mode=RequestedMode.sea,
        reason="maritime document requirements must be prepared for sea shipment",
        priority=FetchPriority.optional,
        required_inputs=[
            RequiredInput(
                field="core_shipment.weight_kg",
                reason="SEA-F needs weight for VGM/document planning",
            ),
            RequiredInput(
                field="cargo_flags.dangerous_goods",
                reason="SEA-F needs DG flag for DG document planning",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.planning_unknown,
        fallback_policy=FallbackPolicy.return_planning_only,
    )


def _sea_i_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-I",
        mode=RequestedMode.sea,
        reason=(
            "sea route, chokepoint, and schedule readiness must be checked "
            "before sea preparation"
        ),
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="SEA-I needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="SEA-I needs destination country",
            ),
            RequiredInput(
                field="lane.origin_city",
                reason="SEA-I uses origin port/city for routing",
            ),
            RequiredInput(
                field="lane.destination_city",
                reason="SEA-I uses destination port/city for routing",
            ),
            RequiredInput(
                field="commercial.ready_date",
                reason="SEA-I needs ready date for schedule readiness",
            ),
            RequiredInput(
                field="commercial.deadline",
                reason="SEA-I needs deadline for urgency assessment",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _sea_cost_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="SEA-COST",
        mode=RequestedMode.sea,
        reason="sea planning cost reference should be checked after readiness facts",
        priority=FetchPriority.optional,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="SEA-COST needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="SEA-COST needs destination country",
            ),
            RequiredInput(
                field="core_shipment.weight_kg",
                reason="SEA-COST uses weight for planning context",
            ),
            RequiredInput(
                field="core_shipment.volume_cbm",
                reason="SEA-COST uses volume for planning context",
            ),
            RequiredInput(
                field="commercial.incoterm",
                reason="SEA-COST uses incoterm for cost responsibility context",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.planning_unknown,
        fallback_policy=FallbackPolicy.return_planning_only,
    )


def _sea_items(request: ValidatedShipmentRequest) -> list[FetchPlanItem]:
    items = [_sea_c_item(), _sea_d_item()]
    if request.cargo_flags.dangerous_goods in {
        FlagState.yes,
        FlagState.likely,
        FlagState.unknown,
    }:
        items.append(_sea_a_item())
    items.extend([_sea_b_item(), _sea_f_item(), _sea_i_item(), _sea_cost_item()])
    return items


def _air_a_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-A",
        mode=RequestedMode.air,
        reason="air dangerous goods acceptance must be checked before air preparation",
        priority=FetchPriority.fail_fast,
        required_inputs=[
            RequiredInput(
                field="cargo_flags.dangerous_goods",
                reason="AIR-A needs DG flag",
            ),
            RequiredInput(
                field="profiles.dangerous_goods.un_number",
                reason="AIR-A needs UN number for DG cargo",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.fail_fast_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_c_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-C",
        mode=RequestedMode.air,
        reason="air airport capability must be checked before air preparation",
        priority=FetchPriority.fail_fast,
        required_inputs=[
            RequiredInput(
                field="lane.origin_city",
                reason="AIR-C needs origin airport/city",
            ),
            RequiredInput(
                field="lane.origin_country",
                reason="AIR-C needs origin country",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.fail_fast_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_b_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-B",
        mode=RequestedMode.air,
        reason="air special handling requirements must be checked before air preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="cargo_flags",
                reason="AIR-B needs cargo flags for special handling planning",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_d_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-D",
        mode=RequestedMode.air,
        reason="air carrier capability must be checked before air preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="cargo_flags",
                reason="AIR-D needs cargo flags for carrier capability triggers",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_e_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-E",
        mode=RequestedMode.air,
        reason="aircraft and ULD fit must be checked for air preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="core_shipment.weight_kg",
                reason="AIR-E needs shipment weight",
            ),
            RequiredInput(
                field="core_shipment.volume_cbm",
                reason="AIR-E needs volume for ULD planning",
            ),
            RequiredInput(
                field="core_shipment.dimensions",
                reason="AIR-E needs dimensions for door/ULD fit",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_f_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-F",
        mode=RequestedMode.air,
        reason="air border and permit requirements must be checked before air preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="AIR-F needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="AIR-F needs destination country",
            ),
            RequiredInput(
                field="cargo_flags",
                reason="AIR-F needs cargo flags for permit triggers",
            ),
            RequiredInput(
                field="commercial.incoterm",
                reason="AIR-F uses incoterm for responsibility planning",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_h_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-H",
        mode=RequestedMode.air,
        reason="air security and screening requirements must be checked before air preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="AIR-H needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="AIR-H needs destination country",
            ),
            RequiredInput(
                field="core_shipment.cargo_description",
                reason=(
                    "AIR-H needs cargo description for security data readiness"
                ),
            ),
            RequiredInput(
                field="cargo_flags",
                reason="AIR-H needs cargo flags for screening triggers",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_i_item() -> FetchPlanItem:
    return FetchPlanItem(
        block_id="AIR-I",
        mode=RequestedMode.air,
        reason="air route and schedule readiness must be checked before air preparation",
        priority=FetchPriority.required,
        required_inputs=[
            RequiredInput(
                field="lane.origin_country",
                reason="AIR-I needs origin country",
            ),
            RequiredInput(
                field="lane.destination_country",
                reason="AIR-I needs destination country",
            ),
            RequiredInput(
                field="lane.origin_city",
                reason="AIR-I uses origin city/airport for routing",
            ),
            RequiredInput(
                field="lane.destination_city",
                reason="AIR-I uses destination city/airport for routing",
            ),
            RequiredInput(
                field="commercial.ready_date",
                reason="AIR-I needs ready date for schedule readiness",
            ),
            RequiredInput(
                field="commercial.deadline",
                reason="AIR-I needs deadline for urgency assessment",
            ),
        ],
        skip_condition=None,
        empty_behavior=EmptyResponseBehavior.soft_unknown,
        fallback_policy=FallbackPolicy.return_unknown,
    )


def _air_items(request: ValidatedShipmentRequest) -> list[FetchPlanItem]:
    items = [_air_c_item(), _air_d_item()]
    if request.cargo_flags.dangerous_goods in {
        FlagState.yes,
        FlagState.likely,
        FlagState.unknown,
    }:
        items.append(_air_a_item())
    if _air_b_should_plan(request):
        items.append(_air_b_item())
    items.append(_air_e_item())
    items.append(_air_f_item())
    items.append(_air_h_item())
    items.append(_air_i_item())
    return items


def _air_b_should_plan(request: ValidatedShipmentRequest) -> bool:
    return any(
        getattr(request.cargo_flags, flag)
        in {FlagState.yes, FlagState.likely, FlagState.unknown}
        for flag in (
            "temperature_controlled",
            "oversized",
            "high_value",
            "pharma",
            "food_perishable",
            "live_animals",
            "dangerous_goods",
        )
    )


def build_fetch_plan(request: ValidatedShipmentRequest) -> FetchPlan:
    items: list[FetchPlanItem] = []
    requested_mode = request.mode.requested_mode

    if requested_mode == RequestedMode.road:
        items.extend(_road_items())
    elif requested_mode == RequestedMode.sea:
        items.extend(_sea_items(request))
    elif requested_mode == RequestedMode.unknown:
        candidate_modes = request.mode.candidate_modes
        if not candidate_modes:
            # Explicit empty candidate_modes is treated like unresolved mode, not as no modes.
            candidate_modes = [
                RequestedMode.sea,
                RequestedMode.air,
                RequestedMode.road,
            ]
        if RequestedMode.sea in candidate_modes:
            items.extend(_sea_items(request))
        if RequestedMode.air in candidate_modes:
            items.extend(_air_items(request))
        if RequestedMode.road in candidate_modes:
            items.extend(_road_items())
    elif requested_mode == RequestedMode.air:
        items.extend(_air_items(request))

    return FetchPlan(case_id=request.case_id, items=items)
