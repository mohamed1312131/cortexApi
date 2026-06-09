import pytest

import app.services.layer2.connectors.road_c_connector as road_c_connector
from app.schemas import (
    BlockStatus,
    GateSeverity,
    GateStatus,
    ProviderUsed,
    RequestedMode,
    SourceConfidence,
)
from app.services.layer2.connectors.road_c_connector import fetch_road_c


@pytest.fixture(autouse=True)
def clear_road_c_cache():
    if hasattr(road_c_connector._load_corridors, "cache_clear"):
        road_c_connector._load_corridors.cache_clear()
    yield
    if hasattr(road_c_connector._load_corridors, "cache_clear"):
        road_c_connector._load_corridors.cache_clear()


def test_road_c_it_fr_found_no_blocking_gate():
    response = fetch_road_c("IT", "FR")

    assert response.status == BlockStatus.found
    assert response.mode == RequestedMode.road
    assert len(response.hard_gates) == 0
    assert response.provenance.provider_used == ProviderUsed.mock
    assert response.provenance.record_id == "IT->FR"
    assert response.data["origin_country"] == "IT"
    assert response.data["destination_country"] == "FR"
    assert "hard_gate" in response.data
    assert response.data["hard_gate"] is False


def test_road_c_cn_fr_found_blocking_gate():
    response = fetch_road_c("CN", "FR")

    assert response.status == BlockStatus.found
    assert response.mode == RequestedMode.road
    assert len(response.hard_gates) == 1
    gate = response.hard_gates[0]
    assert gate.severity == GateSeverity.blocking
    assert gate.status == GateStatus.triggered
    assert gate.source_block == "ROAD-C"
    assert gate.gate_id == response.data["rule_id"]
    assert gate.basis == response.data["corridor_viability"]
    assert response.provenance.provider_used == ProviderUsed.mock
    assert response.provenance.record_id == "CN->FR"


def test_road_c_missing_pair_unknown_not_clear():
    response = fetch_road_c("XX", "YY")

    assert response.status == BlockStatus.unknown
    assert response.mode == RequestedMode.road
    assert len(response.hard_gates) == 0
    assert response.unknowns
    assert response.unknowns[0].field == "road_corridor_viability"
    assert response.confidence.source_confidence == SourceConfidence.unknown
    assert response.provenance.record_id == "XX->YY"


def test_road_c_missing_country_skipped_with_unknown():
    response = fetch_road_c(None, "FR")

    assert response.status == BlockStatus.skipped
    assert response.mode == RequestedMode.road
    assert len(response.hard_gates) == 0
    assert "lane.origin_country" in response.missing_fields
    assert "lane.destination_country" in response.missing_fields
    assert response.unknowns
    assert response.unknowns[0].field == "road_corridor_viability"
    assert response.confidence.source_confidence == SourceConfidence.unknown


def test_road_c_malformed_hard_gate_unknown_not_clear(monkeypatch):
    monkeypatch.setattr(
        road_c_connector,
        "_load_corridors",
        lambda: [
            {
                "origin_country": "AA",
                "destination_country": "BB",
                "rule_id": "TEST_RULE",
                "corridor_viability": "test_viability",
                "_confidence": "verified",
            }
        ],
    )

    response = fetch_road_c("AA", "BB")

    assert response.status == BlockStatus.unknown
    assert response.mode == RequestedMode.road
    assert len(response.hard_gates) == 0
    assert response.unknowns
    assert response.unknowns[0].field == "road_corridor_viability.hard_gate"
    assert response.data["rule_id"] == "TEST_RULE"
    assert response.confidence.source_confidence == SourceConfidence.verified
