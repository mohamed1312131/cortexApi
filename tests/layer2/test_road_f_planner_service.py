from app.schemas import (
    CargoFlags,
    Commercial,
    CoreShipment,
    FlagState,
    BlockStatus,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.service import build_fact_package_for_request


def _road_request() -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-road-f-service",
        lane=Lane(
            origin_city="Milan",
            origin_country="IT",
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=RequestedMode.road,
            candidate_modes=[RequestedMode.road],
            needs_mode_selection=False,
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
        core_shipment=CoreShipment(
            cargo_description="industrial spare parts",
            weight_kg=1200,
            volume_cbm=12.5,
            dimensions=[2.0, 1.5, 1.0],
        ),
        commercial=Commercial(
            incoterm="DAP",
            ready_date="2026-06-10",
            deadline="2026-06-12",
        ),
    )


def test_road_request_plans_road_f_after_road_b_when_road_c_not_blocked():
    plan = build_fetch_plan(_road_request())
    blocks = [item.block_id for item in plan.items]

    assert blocks == ["ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST"]
    assert blocks.index("ROAD-C") < blocks.index("ROAD-B")
    assert blocks.index("ROAD-B") < blocks.index("ROAD-F")


def _oversized_road_request() -> ValidatedShipmentRequest:
    # Oversized machinery genuinely triggers ROAD-B abnormal-movement hard
    # gates. (A plain spare-parts request no longer gates ROAD-B: the old
    # substring matcher hit "long_industrial_beams" via the token
    # "industrial", which was a false positive.)
    request = _road_request()
    return request.model_copy(
        update={
            "cargo_flags": request.cargo_flags.model_copy(
                update={"oversized": FlagState.yes}
            ),
            "core_shipment": request.core_shipment.model_copy(
                update={
                    "cargo_description": "oversized industrial press machine",
                    "weight_kg": 45000,
                    "dimensions": [8.0, 3.2, 4.2],
                }
            ),
        }
    )


def test_layer2_service_road_skips_road_f_after_road_b_blocking_gate():
    package = build_fact_package_for_request(_oversized_road_request())
    blocks = [response.block_id for response in package.block_responses]

    assert blocks == ["ROAD-C", "ROAD-A", "ROAD-B", "ROAD-F", "ROAD-COST"]
    assert blocks.index("ROAD-C") < blocks.index("ROAD-F")
    road_b = next(
        response for response in package.block_responses if response.block_id == "ROAD-B"
    )
    road_f = next(
        response for response in package.block_responses if response.block_id == "ROAD-F"
    )
    assert road_b.hard_gates
    assert road_f.status == BlockStatus.skipped
    assert "ROAD-B" in road_f.unknowns[0].reason
    assert "blocking hard gate" in road_f.unknowns[0].reason
    assert RequestedMode.road in package.derived_rollup.modes_covered
