from app.services.layer2.data_catalog import (
    build_data_catalog,
    find_assets,
    get_main_asset,
)


def test_data_catalog_builds_and_has_assets():
    catalog = build_data_catalog()

    assert catalog
    assert any(asset.mode == "road" for asset in catalog)
    assert any(asset.mode == "sea" for asset in catalog)
    assert any(asset.mode == "air" for asset in catalog)


def test_data_catalog_identifies_core_main_assets():
    road_c = get_main_asset("ROAD-C")
    road_b = get_main_asset("ROAD-B")
    road_f = get_main_asset("ROAD-F")
    road_cost = get_main_asset("ROAD-COST")
    sea_a = get_main_asset("SEA-A")
    sea_b = get_main_asset("SEA-B")
    sea_c = get_main_asset("SEA-C")
    sea_d = get_main_asset("SEA-D")
    sea_i = get_main_asset("SEA-I")
    sea_cost = get_main_asset("SEA-COST")
    air_a = get_main_asset("AIR-A")
    air_b = get_main_asset("AIR-B")
    air_c = get_main_asset("AIR-C")
    air_d = get_main_asset("AIR-D")
    air_e = get_main_asset("AIR-E")
    air_f = get_main_asset("AIR-F")
    air_h = get_main_asset("AIR-H")
    air_i = get_main_asset("AIR-I")

    assert road_c is not None
    assert road_c.path.endswith("road_c_corridor_viability.json")
    assert road_b is not None
    assert road_b.path.endswith("road_b_vehicle_fit_profiles.json")
    assert road_f is not None
    assert road_f.path.endswith("road_f_document_requirements.json")
    assert road_cost is not None
    assert road_cost.path.endswith("road_cost_reference.json")
    assert sea_a is not None
    assert sea_a.path.endswith("sea_a_dg_sea_acceptance.json")
    assert sea_b is not None
    assert sea_b.path.endswith("sea_b_container_fit_rules.json")
    assert sea_c is not None
    assert sea_c.path.endswith("sea_c_port_capability.json")
    assert sea_d is not None
    assert sea_d.path.endswith("sea_d_carrier_trade_lane_reference.json")
    assert sea_i is not None
    assert sea_i.path.endswith("sea_i_chokepoints_schedule_readiness.json")
    assert sea_cost is not None
    assert sea_cost.path.endswith("sea_cost_reference.json")
    assert air_a is not None
    assert air_a.path.endswith("cortex_air_block_a_dg_records_REPAIRED.json")
    assert air_b is not None
    assert air_b.path.endswith("cortex_air_block_b_dataset.json")
    assert air_c is not None
    assert air_c.path.endswith("cortex_air_block_c_dataset.json")
    assert air_d is not None
    assert air_d.path.endswith("cortex_air_block_d_dataset.json")
    assert air_e is not None
    assert air_e.path.endswith("cortex_air_block_e_dataset.json")
    assert air_f is not None
    assert air_f.path.endswith("cortex_air_block_f_dataset.json")
    assert air_h is not None
    assert air_h.path.endswith("cortex_air_block_h_dataset.json")
    assert air_i is not None
    assert air_i.path.endswith("cortex_air_block_i_dataset.json")


def test_data_catalog_connector_statuses():
    road_c = get_main_asset("ROAD-C")
    road_b = get_main_asset("ROAD-B")
    road_f = get_main_asset("ROAD-F")
    road_cost = get_main_asset("ROAD-COST")
    sea_a = get_main_asset("SEA-A")
    sea_d = get_main_asset("SEA-D")
    sea_i = get_main_asset("SEA-I")
    sea_cost = get_main_asset("SEA-COST")
    air_a = get_main_asset("AIR-A")
    air_b = get_main_asset("AIR-B")
    air_c = get_main_asset("AIR-C")
    air_d = get_main_asset("AIR-D")
    air_e = get_main_asset("AIR-E")
    air_f = get_main_asset("AIR-F")
    air_h = get_main_asset("AIR-H")
    air_i = get_main_asset("AIR-I")
    sea_b = get_main_asset("SEA-B")

    assert road_c is not None
    assert road_c.connector_status == "implemented"
    assert road_b is not None
    assert road_b.connector_status == "implemented"
    assert road_f is not None
    assert road_f.connector_status == "implemented"
    assert road_cost is not None
    assert road_cost.connector_status == "implemented"
    assert sea_a is not None
    assert sea_a.connector_status == "implemented"
    assert sea_d is not None
    assert sea_d.connector_status == "implemented"
    assert sea_i is not None
    assert sea_i.connector_status == "implemented"
    assert sea_cost is not None
    assert sea_cost.connector_status == "implemented"
    assert air_a is not None
    assert air_a.connector_status == "implemented"
    assert air_b is not None
    assert air_b.connector_status == "implemented"
    assert air_c is not None
    assert air_c.connector_status == "implemented"
    assert air_d is not None
    assert air_d.connector_status == "implemented"
    assert air_e is not None
    assert air_e.connector_status == "implemented"
    assert air_f is not None
    assert air_f.connector_status == "implemented"
    assert air_h is not None
    assert air_h.connector_status == "implemented"
    assert air_i is not None
    assert air_i.connector_status == "implemented"
    assert sea_b is not None
    assert sea_b.connector_status == "implemented"


def test_data_catalog_no_invalid_json():
    catalog = build_data_catalog()

    assert not [asset for asset in catalog if asset.top_type == "invalid"]


def test_find_assets_filters():
    sea_main_assets = find_assets(mode="sea", role="main")
    sea_main_block_ids = {asset.block_id for asset in sea_main_assets}

    assert "SEA-A" in sea_main_block_ids
    assert "SEA-B" in sea_main_block_ids
    assert "SEA-C" in sea_main_block_ids
    assert "SEA-D" in sea_main_block_ids
    assert "SEA-I" in sea_main_block_ids
    assert "SEA-COST" in sea_main_block_ids
    assert find_assets(block_id="SEA-A")
