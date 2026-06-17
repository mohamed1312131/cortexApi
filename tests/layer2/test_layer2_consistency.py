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
from app.services.layer2.data_catalog import build_data_catalog, get_main_asset
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.registry import get_connector
from app.services.layer2.service import build_fact_package_for_request

IMPLEMENTED_BLOCKS = [
    "AIR-A",
    "AIR-B",
    "AIR-C",
    "AIR-D",
    "AIR-E",
    "AIR-F",
    "AIR-H",
    "AIR-I",
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
]


def test_registry_contains_all_implemented_blocks():
    for block_id in IMPLEMENTED_BLOCKS:
        assert get_connector(block_id) is not None


def test_catalog_status_matches_implemented_registry():
    catalog = build_data_catalog()

    for asset in catalog:
        if asset.connector_status == "implemented" and asset.block_id is not None:
            assert get_connector(asset.block_id) is not None


def test_catalog_has_main_asset_for_each_implemented_or_partial_block():
    catalog = build_data_catalog()

    for block_id in IMPLEMENTED_BLOCKS:
        main_asset = get_main_asset(block_id)
        if block_id == "ROAD-A" and main_asset is None:
            assert any(asset.block_id == "ROAD-A" for asset in catalog)
        else:
            assert main_asset is not None


def test_planner_orders_road_and_sea_blocks():
    road_plan = build_fetch_plan(_road_request())
    road_blocks = [item.block_id for item in road_plan.items]
    assert road_blocks[0] == "ROAD-C"
    assert road_blocks.index("ROAD-B") < road_blocks.index("ROAD-F")
    assert road_blocks[-1] == "ROAD-COST"

    sea_dg_plan = build_fetch_plan(_sea_request(dangerous_goods=FlagState.yes))
    assert [item.block_id for item in sea_dg_plan.items] == [
        "SEA-C",
        "SEA-D",
        "SEA-A",
        "SEA-B",
        "SEA-F",
        "SEA-I",
        "SEA-COST",
    ]

    sea_non_dg_plan = build_fetch_plan(_sea_request(dangerous_goods=FlagState.no))
    sea_non_dg_blocks = [item.block_id for item in sea_non_dg_plan.items]
    assert "SEA-A" not in sea_non_dg_blocks
    assert sea_non_dg_blocks.index("SEA-C") < sea_non_dg_blocks.index("SEA-D")
    assert sea_non_dg_blocks.index("SEA-D") < sea_non_dg_blocks.index("SEA-B")
    assert sea_non_dg_blocks.index("SEA-C") < sea_non_dg_blocks.index("SEA-B")
    assert sea_non_dg_blocks.index("SEA-C") < sea_non_dg_blocks.index("SEA-F")
    assert sea_non_dg_blocks.index("SEA-F") < sea_non_dg_blocks.index("SEA-I")
    assert sea_non_dg_blocks.index("SEA-I") < sea_non_dg_blocks.index("SEA-COST")


def test_no_connector_returns_raw_dataset_file_as_data():
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
        "carrier_capabilities",
        "airport_capabilities",
        "special_handling_codes",
        "category_rules",
        "confidence_rules",
        "source_refs",
        "aircraft_fit_specs",
        "uld_specs",
        "airport_jurisdiction_map",
        "border_rules",
        "security_status_matrix",
        "jurisdiction_security_rules",
        "placi_minimum_data",
        "route_feasibility_rules",
        "route_risk_rules",
        "schedule_input_requirements",
        # NOTE: "tracking_milestones" is intentionally NOT blocklisted — AIR-I
        # surfaces a curated, enriched tracking-milestone projection (each item
        # carries an added planning_value), which is legitimate report content,
        # not a raw dataset dump.
        "fit_rules",
    }

    for request in [
        _road_request(),
        _sea_request(dangerous_goods=FlagState.yes),
        _air_request(),
    ]:
        package = build_fact_package_for_request(request)
        for response in package.block_responses:
            assert isinstance(response.data, dict)
            assert not raw_dataset_keys.intersection(response.data)


def _road_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-consistency-road",
        lane=Lane(origin_country="IT", destination_country="FR"),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(dangerous_goods=FlagState.no),
        core_shipment=CoreShipment(
            weight_kg=1200,
            dimensions=[2.0, 1.5, 1.0],
        ),
    )


def _sea_request(dangerous_goods: FlagState) -> ValidatedShipmentRequest:
    profiles = {}
    if dangerous_goods in {FlagState.yes, FlagState.likely}:
        profiles = {"dangerous_goods": {"un_number": "UN1410"}}

    return ValidatedShipmentRequest(
        case_id="case-consistency-sea",
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
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(incoterm="FOB"),
        profiles=profiles,
    )


def _air_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-consistency-air",
        lane=Lane(
            origin_city="Shanghai",
            origin_country="CN",
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
            needs_mode_selection=False,
        ),
        cargo_flags=CargoFlags(dangerous_goods=FlagState.yes),
        core_shipment=CoreShipment(
            weight_kg=1200,
            volume_cbm=4.5,
            dimensions=[1.0, 1.0, 1.0],
        ),
        profiles={"dangerous_goods": {"un_number": "UN3480"}},
    )
