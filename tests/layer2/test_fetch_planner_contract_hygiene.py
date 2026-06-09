from pathlib import Path

from app.schemas import (
    CargoFlags,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer2.fetch_planner import build_fetch_plan


ROOT = Path(__file__).resolve().parents[2]


def _request(
    requested_mode: RequestedMode,
    candidate_modes: list[RequestedMode],
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-layer2-plan-hygiene-001",
        lane=Lane(
            origin_city="Tunis",
            origin_country="TN",
            destination_city="Paris",
            destination_country="FR",
        ),
        mode=ModeSelection(
            requested_mode=requested_mode,
            candidate_modes=candidate_modes,
        ),
        cargo_flags=CargoFlags(),
    )


def _block_ids(request: ValidatedShipmentRequest) -> list[str]:
    return [item.block_id for item in build_fetch_plan(request).items]


def test_unknown_mode_empty_candidate_modes_does_not_return_empty_plan():
    request = _request(
        requested_mode=RequestedMode.unknown,
        candidate_modes=[],
    )

    block_ids = _block_ids(request)

    assert block_ids
    assert {"SEA-C", "AIR-C", "ROAD-C"}.issubset(block_ids)


def test_unknown_mode_empty_candidate_modes_uses_default_modes():
    request = _request(
        requested_mode=RequestedMode.unknown,
        candidate_modes=[],
    )

    plan = build_fetch_plan(request)

    assert {item.mode for item in plan.items} == {
        RequestedMode.sea,
        RequestedMode.air,
        RequestedMode.road,
    }


def test_concrete_mode_requested_mode_wins_even_if_candidate_modes_empty():
    request = _request(
        requested_mode=RequestedMode.road,
        candidate_modes=[],
    )

    block_ids = _block_ids(request)

    assert "ROAD-C" in block_ids


def test_concrete_mode_requested_mode_wins_even_if_candidate_modes_conflicts():
    request = _request(
        requested_mode=RequestedMode.road,
        candidate_modes=[RequestedMode.sea],
    )

    block_ids = _block_ids(request)

    assert "ROAD-C" in block_ids
    assert "SEA-C" not in block_ids


def test_air_todo_removed_or_no_stale_air_todo():
    planner_source = (ROOT / "app/services/layer2/fetch_planner.py").read_text()

    assert "TODO: add remaining AIR planning blocks" not in planner_source
