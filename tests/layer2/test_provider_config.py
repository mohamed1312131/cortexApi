from app.schemas import ProviderUsed
from app.services.layer2.connectors.road_c_connector import fetch_road_c
from app.services.layer2.provider_config import get_layer2_provider, provenance_for


def test_default_layer2_provider_is_mock(monkeypatch):
    monkeypatch.delenv("LAYER2_PROVIDER", raising=False)
    monkeypatch.delenv("LAYER2_PROVIDER_OVERRIDES", raising=False)

    assert get_layer2_provider("ROAD-C") == ProviderUsed.mock


def test_layer2_provider_accepts_global_live(monkeypatch):
    monkeypatch.setenv("LAYER2_PROVIDER", "live")
    monkeypatch.delenv("LAYER2_PROVIDER_OVERRIDES", raising=False)

    assert get_layer2_provider("ROAD-C") == ProviderUsed.live


def test_layer2_provider_invalid_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("LAYER2_PROVIDER", "banana")
    monkeypatch.delenv("LAYER2_PROVIDER_OVERRIDES", raising=False)

    assert get_layer2_provider("ROAD-C") == ProviderUsed.mock


def test_layer2_provider_override_wins(monkeypatch):
    monkeypatch.setenv("LAYER2_PROVIDER", "mock")
    monkeypatch.setenv("LAYER2_PROVIDER_OVERRIDES", "ROAD-C=live,SEA-C=mock")

    assert get_layer2_provider("ROAD-C") == ProviderUsed.live
    assert get_layer2_provider("SEA-C") == ProviderUsed.mock
    assert get_layer2_provider("AIR-C") == ProviderUsed.mock


def test_provenance_for_uses_provider_config(monkeypatch):
    monkeypatch.setenv("LAYER2_PROVIDER", "live")
    monkeypatch.delenv("LAYER2_PROVIDER_OVERRIDES", raising=False)

    provenance = provenance_for(
        "ROAD-C",
        "data/road/road_c_corridor_viability.json",
        "IT->FR",
    )

    assert provenance.provider_used == ProviderUsed.live
    assert provenance.record_id == "IT->FR"


def test_connector_default_provenance_still_mock(monkeypatch):
    monkeypatch.delenv("LAYER2_PROVIDER", raising=False)
    monkeypatch.delenv("LAYER2_PROVIDER_OVERRIDES", raising=False)

    response = fetch_road_c("IT", "FR")

    assert response.provenance.provider_used == ProviderUsed.mock


def test_connector_can_surface_live_provider_when_configured(monkeypatch):
    monkeypatch.delenv("LAYER2_PROVIDER", raising=False)
    monkeypatch.setenv("LAYER2_PROVIDER_OVERRIDES", "ROAD-C=live")

    response = fetch_road_c("IT", "FR")

    assert response.provenance.provider_used == ProviderUsed.live
