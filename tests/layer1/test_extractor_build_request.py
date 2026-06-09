# tests/layer1/test_extractor_build_request.py
from app.services.layer1.extractor import IntakeExtraction, _build_request
from app.schemas import RequestedMode, FlagState


def test_build_request_lithium_shenzhen_paris_road():
    ext = IntakeExtraction(
        cargo_description="lithium batteries",
        weight_kg=8000,
        origin_city="Shenzhen",
        destination_city="Paris",
        requested_mode=RequestedMode.road,
        dangerous_goods=FlagState.likely,
        un_number=None,
    )

    req = _build_request(case_id="case-test-001", ext=ext)

    assert req.case_id == "case-test-001"

    assert req.core_shipment.cargo_description == "lithium batteries"
    assert req.core_shipment.weight_kg == 8000

    assert req.lane.origin_city == "Shenzhen"
    assert req.lane.destination_city == "Paris"
    assert req.lane.origin_country == "CN"
    assert req.lane.destination_country == "FR"

    assert req.mode.requested_mode == RequestedMode.road
    assert req.mode.candidate_modes == [RequestedMode.road]
    assert req.mode.needs_mode_selection is False

    assert req.cargo_flags.dangerous_goods == FlagState.likely
    assert "dangerous_goods" in req.active_profiles
    assert req.profiles["dangerous_goods"]["un_number"] is None

    assert "UN number" in req.missing_fields.blocking
    assert req.ready_for_layer_2 is True