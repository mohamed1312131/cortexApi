from app.schemas import (
    BlockStatus,
    CargoFlags,
    FlagState,
    GateSeverity,
    GateStatus,
    RequestedMode,
    SourceConfidence,
    ValidatedShipmentRequest,
)
from app.services.layer2.connectors.road_a_connector import fetch_road_a


def _request(*, dg: FlagState, un: str | None = None) -> ValidatedShipmentRequest:
    profiles = {"dangerous_goods": {"un_number": un}} if un is not None else {}
    return ValidatedShipmentRequest(
        case_id="case-road-a",
        cargo_flags=CargoFlags(dangerous_goods=dg),
        active_profiles=["dangerous_goods"] if dg != FlagState.no else [],
        profiles=profiles,
    )


# --- unchanged early branches -------------------------------------------- #
def test_dg_no_is_not_applicable():
    resp = fetch_road_a(_request(dg=FlagState.no))
    assert resp.status is BlockStatus.not_applicable


def test_dg_unknown_is_unknown():
    resp = fetch_road_a(_request(dg=FlagState.unknown))
    assert resp.status is BlockStatus.unknown
    assert resp.unknowns[0].field == "cargo_flags.dangerous_goods"


def test_dg_yes_without_un_number_asks_for_it():
    resp = fetch_road_a(_request(dg=FlagState.yes))
    assert resp.status is BlockStatus.unknown
    assert "profiles.dangerous_goods.un_number" in resp.missing_fields


# --- new data-driven lookup ---------------------------------------------- #
def test_known_un_returns_record_with_planning_factors():
    # UN1203 gasoline: accepted_with_conditions, tunnel (D/E), no hard gate.
    resp = fetch_road_a(_request(dg=FlagState.yes, un="UN1203"))
    assert resp.status is BlockStatus.found
    assert resp.data["proper_shipping_name"].lower().startswith("motor spirit")
    assert resp.data["hazard_class"] == "3"
    assert resp.data["adr_tunnel_code"] == "(D/E)"
    assert resp.data["road_acceptance_status"] == "accepted_with_conditions"
    assert resp.hard_gates == []
    assert any("tunnel" in f.lower() for f in resp.planning_factors)
    assert resp.confidence.source_confidence is SourceConfidence.verified


def test_lithium_un3480_on_road_is_restricted_but_not_gated():
    # Key mode distinction: UN3480 is a HARD GATE on air (CAO), but on road it is
    # Class 9 tunnel E, shippable under ADR -> no blocking gate.
    resp = fetch_road_a(_request(dg=FlagState.yes, un="UN3480"))
    assert resp.status is BlockStatus.found
    assert resp.data["road_acceptance_status"] == "restricted"
    assert resp.hard_gates == []
    assert resp.data["adr_tunnel_code"] == "(E)"


def test_check_required_un_flags_substance_detail_unknown():
    # UN1993 flammable liquid n.o.s.: entry-dependent -> check_required + unknown.
    resp = fetch_road_a(_request(dg=FlagState.yes, un="UN1993"))
    assert resp.status is BlockStatus.found
    assert resp.data["road_acceptance_status"] == "check_required"
    assert any(u.field == "profiles.dangerous_goods.substance_detail" for u in resp.unknowns)


def test_un_normalization_handles_lowercase_and_spaces():
    resp = fetch_road_a(_request(dg=FlagState.yes, un="un 1203"))
    assert resp.status is BlockStatus.found
    assert resp.data["identification_number"] == "UN1203"


def test_unknown_un_degrades_to_specialist_validation():
    resp = fetch_road_a(_request(dg=FlagState.yes, un="UN9999"))
    assert resp.status is BlockStatus.unknown
    assert resp.data["adr_check"] == "requires_specialist_validation"
    assert resp.confidence.source_confidence is SourceConfidence.unknown
