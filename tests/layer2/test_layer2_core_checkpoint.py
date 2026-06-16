from app.schemas import (
    CargoFlags,
    Commercial,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.registry import get_connector
from app.services.layer2.service import build_fact_package_for_request

CORE_BLOCKS = [
    "ROAD-C",
    "ROAD-A",
    "ROAD-B",
    "ROAD-F",
    "ROAD-COST",
    "SEA-C",
    "SEA-D",
    "SEA-A",
    "SEA-B",
    "SEA-F",
    "SEA-I",
    "SEA-COST",
    "AIR-C",
    "AIR-D",
    "AIR-A",
    "AIR-B",
    "AIR-E",
    "AIR-F",
    "AIR-H",
    "AIR-I",
    "AIR-COST",
]


def test_all_core_blocks_registered():
    for block_id in CORE_BLOCKS:
        assert get_connector(block_id) is not None


def test_planner_order_for_core_modes():
    road_plan = build_fetch_plan(_road_request())
    road_blocks = [item.block_id for item in road_plan.items]
    assert road_blocks[0] == "ROAD-C"
    assert road_blocks.index("ROAD-B") < road_blocks.index("ROAD-F")
    assert road_blocks[-1] == "ROAD-COST"

    sea_dg_plan = build_fetch_plan(
        _sea_request(dangerous_goods=FlagState.yes, un_number="UN1410")
    )
    assert [item.block_id for item in sea_dg_plan.items] == [
        "SEA-C",
        "SEA-D",
        "SEA-A",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]

    sea_non_dg_plan = build_fetch_plan(
        _sea_request(dangerous_goods=FlagState.no)
    )
    sea_non_dg_blocks = [item.block_id for item in sea_non_dg_plan.items]
    assert "SEA-A" not in sea_non_dg_blocks
    assert sea_non_dg_blocks.index("SEA-C") < sea_non_dg_blocks.index("SEA-D")
    assert sea_non_dg_blocks.index("SEA-D") < sea_non_dg_blocks.index("SEA-B")
    assert sea_non_dg_blocks.index("SEA-C") < sea_non_dg_blocks.index("SEA-B")
    assert sea_non_dg_blocks.index("SEA-B") < sea_non_dg_blocks.index("SEA-F")
    assert sea_non_dg_blocks.index("SEA-F") < sea_non_dg_blocks.index("SEA-I")
    assert sea_non_dg_blocks.index("SEA-I") < sea_non_dg_blocks.index("SEA-COST")

    air_dg_special_plan = build_fetch_plan(
        _air_request(
            cargo_flags=_air_flags(
                dangerous_goods=FlagState.yes,
                temperature_controlled=FlagState.yes,
            ),
            un_number="UN3480",
        )
    )
    assert [item.block_id for item in air_dg_special_plan.items] == [
        "AIR-C",
        "AIR-D",
        "AIR-A",
        "AIR-B",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
        "AIR-COST",
    ]

    air_non_dg_plan = build_fetch_plan(_air_request(cargo_flags=_air_flags()))
    air_non_dg_blocks = [item.block_id for item in air_non_dg_plan.items]
    assert "AIR-A" not in air_non_dg_blocks
    assert "AIR-B" not in air_non_dg_blocks
    assert air_non_dg_blocks == [
        "AIR-C",
        "AIR-D",
        "AIR-E",
        "AIR-F",
        "AIR-H",
        "AIR-I",
        "AIR-COST",
    ]


def test_fact_package_modes_covered_for_each_mode():
    road_package = build_fact_package_for_request(_road_request())
    sea_package = build_fact_package_for_request(
        _sea_request(dangerous_goods=FlagState.no)
    )
    air_package = build_fact_package_for_request(
        _air_request(cargo_flags=_air_flags())
    )

    assert RequestedMode.road in road_package.derived_rollup.modes_covered
    assert RequestedMode.sea in sea_package.derived_rollup.modes_covered
    assert RequestedMode.air in air_package.derived_rollup.modes_covered


def test_no_raw_dataset_dumping_any_core_mode():
    raw_dataset_keys = {
        "records",
        "vehicle_profiles",
        "standard_limits",
        "abnormal_load_rules",
        "document_requirements",
        "driver_hours_rules",
        "border_buffer_reference",
        "realistic_transit_model",
        "metadata",
        "cost_reference",
        "container_specs",
        "cargo_type_equipment_mapping",
        "ports",
        "carrier_profiles",
        "trade_lane_families",
        "readiness_rules",
        "chokepoints",
        "schedule_readiness_rules",
        "lane_benchmarks",
        "surcharge_reference",
        "airport_capabilities",
        "carrier_capabilities",
        "aircraft_fit_specs",
        "uld_specs",
        "special_handling_codes",
        "category_rules",
        "border_rules",
        "jurisdiction_security_rules",
        "route_feasibility_rules",
        "route_risk_rules",
        "confidence_rules",
        "source_refs",
    }

    for request in _core_mode_requests():
        package = build_fact_package_for_request(request)
        for response in package.block_responses:
            assert isinstance(response.data, dict)
            assert not raw_dataset_keys.intersection(response.data)


def test_core_connectors_expose_provenance():
    for request in _core_mode_requests():
        package = build_fact_package_for_request(request)
        for response in package.block_responses:
            assert response.provenance.source
            assert response.provenance.provider_used is not None
            assert response.block_id
            assert response.mode != RequestedMode.unknown


def _core_mode_requests() -> list[ValidatedShipmentRequest]:
    return [
        _road_request(),
        _sea_request(dangerous_goods=FlagState.no),
        _air_request(cargo_flags=_air_flags()),
    ]


def _road_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-core-road",
        lane=Lane(origin_country="IT", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(dangerous_goods=FlagState.no),
        core_shipment=CoreShipment(
            cargo_description="industrial spare parts",
            weight_kg=1200,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(incoterm="DAP"),
    )


def _sea_request(
    *,
    dangerous_goods: FlagState,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-core-sea",
        lane=Lane(
            origin_city="Shanghai",
            origin_country="CN",
            destination_city="Marseille",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.sea,
            candidate_modes=[RequestedMode.sea],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(dangerous_goods=dangerous_goods),
        core_shipment=CoreShipment(
            cargo_description="machinery parts",
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
            quantity=3,
            packaging="crates",
        ),
        commercial=Commercial(incoterm="FOB"),
        profiles=profiles,
    )


def _air_request(
    *,
    cargo_flags: CargoFlags,
    un_number: str | None = None,
) -> ValidatedShipmentRequest:
    profiles = {}
    if un_number is not None:
        profiles = {"dangerous_goods": {"un_number": un_number}}

    return ValidatedShipmentRequest(
        case_id="case-core-air",
        lane=Lane(
            origin_city="Paris",
            origin_country="FR",
            destination_city="New York",
            destination_country="US",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
            needs_mode_selection=False,
        ),
        cargo_flags=cargo_flags,
        core_shipment=CoreShipment(
            cargo_description="electronics spare parts",
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
            quantity=2,
            packaging="cartons",
        ),
        commercial=Commercial(
            incoterm="DAP",
            ready_date="2026-06-10",
            deadline="2026-06-12",
        ),
        profiles=profiles,
    )


def _air_flags(**overrides: FlagState) -> CargoFlags:
    values = {
        "dangerous_goods": FlagState.no,
        "temperature_controlled": FlagState.no,
        "oversized": FlagState.no,
        "high_value": FlagState.no,
        "pharma": FlagState.no,
        "food_perishable": FlagState.no,
        "live_animals": FlagState.no,
    }
    values.update(overrides)
    return CargoFlags(**values)
