# tests/layer1/test_extractor_live.py
import os
import pytest

from app.services.layer1.extractor import extract_shipment
from app.schemas import RequestedMode, FlagState


pytestmark = pytest.mark.live_llm


@pytest.mark.skipif(
    not os.getenv("GOOGLE_AI_API_KEY"),
    reason="GOOGLE_AI_API_KEY not set; skipping live Gemma extraction test",
)
def test_live_gemma_extracts_shenzhen_paris_road():
    req = extract_shipment(
        case_id="case-live-001",
        message="I need to ship 8 tons of lithium batteries from Shenzhen to Paris by road.",
    )

    assert req.core_shipment.weight_kg == 8000
    assert req.lane.origin_city.lower() == "shenzhen"
    assert req.lane.destination_city.lower() == "paris"
    assert req.lane.origin_country == "CN"
    assert req.lane.destination_country == "FR"
    assert req.mode.requested_mode == RequestedMode.road

    assert req.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}
    assert "dangerous_goods" in req.active_profiles