from app.schemas import (
    BlockStatus,
    CargoFlags,
    Commercial,
    CoreShipment,
    FlagState,
    Lane,
    ModeSelection,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.air_cost_connector import fetch_air_cost


def _request(
    *,
    weight=None,
    volume=None,
    dimensions=None,
    origin_country=None,
    destination_country=None,
    incoterm=None,
    dg=FlagState.no,
) -> ValidatedShipmentRequest:
    return ValidatedShipmentRequest(
        case_id="case-air-cost",
        core_shipment=CoreShipment(weight_kg=weight, volume_cbm=volume, dimensions=dimensions),
        lane=Lane(origin_country=origin_country, destination_country=destination_country),
        commercial=Commercial(incoterm=incoterm),
        cargo_flags=CargoFlags(dangerous_goods=dg),
        mode=ModeSelection(
            requested_mode=RequestedMode.air,
            candidate_modes=[RequestedMode.air],
            needs_mode_selection=False,
        ),
    )


def test_always_planning_reference_never_a_quote():
    resp = fetch_air_cost(_request(weight=8000, origin_country="CN", destination_country="DE"))
    assert resp.status is BlockStatus.unknown  # cost is never authoritative
    assert resp.data["cost_status"] == "planning_reference_not_a_quote"
    assert resp.confidence.source_confidence is SourceConfidence.planning_reference
    assert resp.confidence.cap == 0.5


def test_asia_to_europe_rate_bucket_and_cost_band():
    resp = fetch_air_cost(
        _request(weight=8000, volume=40, origin_country="CN", destination_country="DE")
    )
    d = resp.data
    assert d["rate_basis"] == "asia_to_europe"
    # chargeable = max(8000 actual, 40*167=6680 volumetric) = 8000
    assert d["actual_weight_kg"] == 8000
    assert d["volumetric_weight_kg"] == 6680.0
    assert d["chargeable_weight_kg"] == 8000
    assert d["chargeable_weight_basis"] == "max(actual, volumetric)"
    band = d["estimated_cost_usd"]
    assert band["low"] < band["typical"] < band["high"]
    # sanity: 8000kg * ~5.5 typical * ~1.35 surcharge is tens of thousands USD
    assert band["typical"] > 10000
    assert d["transit_days_door_to_door"] == {"low": 2, "typical": 4, "high": 7}


def test_low_density_cargo_charged_on_volume():
    # 200 kg actual but 50 cbm -> volumetric 50*167=8350 dominates
    resp = fetch_air_cost(
        _request(weight=200, volume=50, origin_country="CN", destination_country="US")
    )
    d = resp.data
    assert d["rate_basis"] == "asia_to_north_america"
    assert d["chargeable_weight_kg"] == 8350.0
    assert d["chargeable_weight_basis"] == "max(actual, volumetric)"


def test_missing_weight_and_volume_cannot_estimate():
    resp = fetch_air_cost(_request(origin_country="CN", destination_country="DE"))
    assert resp.data["estimated_cost_usd"] is None
    assert "core_shipment.weight_kg" in resp.missing_fields
    assert any(u.field == "core_shipment.weight_kg" for u in resp.unknowns)


def test_missing_volume_flags_volumetric_unknown_but_still_estimates():
    resp = fetch_air_cost(_request(weight=5000, origin_country="CN", destination_country="DE"))
    d = resp.data
    assert d["chargeable_weight_basis"] == "actual_only_volumetric_unknown"
    assert d["estimated_cost_usd"] is not None
    assert any(u.field == "core_shipment.volume_cbm" for u in resp.unknowns)


def test_dangerous_goods_raises_the_estimate():
    base = fetch_air_cost(
        _request(weight=3000, volume=10, origin_country="CN", destination_country="DE")
    ).data["estimated_cost_usd"]
    dg = fetch_air_cost(
        _request(weight=3000, volume=10, origin_country="CN", destination_country="DE", dg=FlagState.yes)
    ).data["estimated_cost_usd"]
    assert dg["typical"] > base["typical"]
    assert dg["high"] > base["high"]


def test_unknown_lane_falls_back_to_general_default():
    resp = fetch_air_cost(_request(weight=1000, origin_country="BR", destination_country="ZA"))
    assert resp.data["rate_basis"] == "general_default"
    assert any("general default" in f.lower() for f in resp.planning_factors)
