"""Real Layer 1 -> Layer 2 seam tests for /api/v1/cortex/message.

These tests deliberately exercise the *real* Layer 1 graph (routing, validation,
conflict detection, multi-shipment handling, missing-field prioritization, the
decision engine, persistence and sanitization). The only non-deterministic part
of Layer 1 is the LLM field extractor (`extract_shipment`), so that single call
is stubbed with deterministic fakes. Everything downstream of it is the genuine
production code path.

Layer 2 (`build_fact_package_for_request`) is wrapped by a spy that records the
argument and either refuses to run (unsafe/incomplete intake) or calls through to
the real deterministic fact builder (ready intake).
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    CoreShipment,
    Lane,
    ModeSelection,
    RequestedMode,
    ValidatedShipmentRequest,
)
from app.services.layer1.case_state_manager import case_state_store
from app.services.layer1.extractor import MultipleShipmentDetected
import app.services.orchestrator.cortex_orchestrator as orchestrator
from app.services.layer2.service import (
    build_fact_package_for_request as real_build_fact_package,
)

# The `nodes` package re-exports the `extract_shipment_fields` *function*, which
# shadows the submodule attribute, so resolve the real module explicitly.
extract_node = importlib.import_module(
    "app.services.layer1.nodes.extract_shipment_fields"
)


# --------------------------------------------------------------------------- #
# Deterministic stand-in for the LLM extractor (the only non-deterministic part
# of the real Layer 1 graph). Everything else in Layer 1 runs for real.
# --------------------------------------------------------------------------- #
def _vsr(
    case_id: str,
    *,
    cargo: str | None = None,
    weight: float | None = None,
    volume: float | None = None,
    ocity: str | None = None,
    dcity: str | None = None,
    oraw: str | None = None,
    draw: str | None = None,
    ocountry: str | None = None,
    dcountry: str | None = None,
    un: str | None = None,
    mode: RequestedMode = RequestedMode.unknown,
) -> ValidatedShipmentRequest:
    profiles: dict = {}
    if un is not None:
        profiles["dangerous_goods"] = {"un_number": un}
    if mode is RequestedMode.unknown:
        mode_sel = ModeSelection()
    else:
        mode_sel = ModeSelection(
            requested_mode=mode,
            candidate_modes=[mode],
            needs_mode_selection=False,
        )
    return ValidatedShipmentRequest(
        case_id=case_id,
        core_shipment=CoreShipment(cargo_description=cargo, weight_kg=weight, volume_cbm=volume),
        lane=Lane(
            origin_city=ocity,
            destination_city=dcity,
            origin_raw=oraw or ocity,
            destination_raw=draw or dcity,
            origin_country=ocountry,
            destination_country=dcountry,
        ),
        mode=mode_sel,
        profiles=profiles,
    )


def _fake_extract_shipment(case_id: str, message: str) -> ValidatedShipmentRequest:
    text = message.lower()

    if "two shipments" in text:
        raise MultipleShipmentDetected("multiple shipments detected (test stub)")

    if "perfume" in text:
        # cargo / UN conflict: perfume cannot carry a lithium UN number.
        return _vsr(
            case_id,
            cargo="perfume",
            weight=500,
            ocity="Grasse",
            dcity="Dubai",
            ocountry="FR",
            dcountry="AE",
            un="UN3480",
        )

    if "textile" in text:
        return _vsr(
            case_id,
            cargo="textile",
            weight=500,
            ocity="Milan",
            dcity="Paris",
            ocountry="IT",
            dcountry="FR",
            mode=RequestedMode.road,
        )

    # Check the valid 4-digit UN number before the malformed one, because the
    # string "un3480" also contains the substring "un348".
    if "un3480" in text:
        return _vsr(
            case_id,
            cargo="lithium batteries",
            weight=8000,
            ocity="Shenzhen",
            dcity="Lyon",
            ocountry="CN",
            dcountry="FR",
            un="UN3480",
        )

    if "un348" in text:
        # Malformed UN number; the real validator must reject it.
        return _vsr(
            case_id,
            cargo="lithium batteries",
            weight=8000,
            ocity="Shenzhen",
            dcity="Lyon",
            ocountry="CN",
            dcountry="FR",
            un="UN348",
        )

    if "lithium batteries" in text:
        # Incomplete lithium intake: no weight, no cities, no UN number.
        return _vsr(
            case_id,
            cargo="lithium batteries",
            oraw="China",
            draw="France",
            ocountry="CN",
            dcountry="FR",
        )

    return ValidatedShipmentRequest(case_id=case_id)


class _Layer2Spy:
    """Records Layer 2 calls. Refuses to run unless call_through is enabled."""

    def __init__(self, *, call_through: bool) -> None:
        self.calls: list[ValidatedShipmentRequest] = []
        self.call_through = call_through

    def __call__(self, request: ValidatedShipmentRequest):
        self.calls.append(request)
        if self.call_through:
            return real_build_fact_package(request)
        raise AssertionError("Layer 2 must not be called for unsafe/incomplete intake")


@pytest.fixture(autouse=True)
def _clear_case_state():
    # The Layer 1 graph uses a process-wide store (Redis with an in-memory
    # fallback). Reset the in-memory maps between tests for isolation. Each test
    # also uses a unique conversation_id, so cross-test leakage is avoided even
    # when a real Redis backs the store.
    def _reset() -> None:
        store = getattr(case_state_store, "_fallback", case_state_store)
        if hasattr(store, "_cases"):
            store._cases.clear()
        if hasattr(store, "_conversation_active_case"):
            store._conversation_active_case.clear()

    _reset()
    yield
    _reset()


@pytest.fixture
def real_layer1(monkeypatch):
    """Stub only the LLM extractor; the rest of Layer 1 runs for real."""
    monkeypatch.setattr(extract_node, "extract_shipment", _fake_extract_shipment)


def _install_layer2_spy(monkeypatch, *, call_through: bool) -> _Layer2Spy:
    spy = _Layer2Spy(call_through=call_through)
    monkeypatch.setattr(orchestrator, "build_fact_package_for_request", spy)
    return spy


# --------------------------------------------------------------------------- #
# A. /api/v1/intake/message is Layer 1 only and never reaches Layer 2.
# --------------------------------------------------------------------------- #
def test_A_intake_message_never_calls_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=False)
    # Also guard the source symbol the orchestrator would import.
    monkeypatch.setattr(
        "app.services.layer2.service.build_fact_package_for_request", spy
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/intake/message",
            json={
                "conversation_id": "seam-A",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    # IntakeResult shape, not an orchestrator result.
    assert "intake_json" in payload
    assert "next_action" not in payload
    assert "layer2" not in payload
    assert spy.calls == []


# --------------------------------------------------------------------------- #
# B. Incomplete lithium shipment -> ASK_USER, no Layer 2.
# --------------------------------------------------------------------------- #
def test_B_incomplete_lithium_does_not_call_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-B",
                "message": "I need to ship lithium batteries from China to France.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ASK_USER"
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is False
    assert payload["layer1"]["ready_for_layer_2"] is False
    assert len(spy.calls) == 0


# --------------------------------------------------------------------------- #
# C. Invalid UN number -> ASK_USER, not autocorrected, no Layer 2.
# --------------------------------------------------------------------------- #
def test_C_invalid_un_does_not_call_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-C",
                "message": "Ship lithium batteries UN348 from Shenzhen to Lyon, 8000 kg.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ASK_USER"
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is False
    assert len(spy.calls) == 0

    # UN348 must NOT be silently autocorrected into a valid 4-digit UN number.
    dg = payload["layer1"]["intake_json"]["profiles"].get("dangerous_goods", {})
    assert dg.get("un_number") is None
    rejected = payload["layer1"]["intake_json"]["inferred_flags"].get("rejected_fields", [])
    assert any(item.get("value") == "UN348" for item in rejected)


# --------------------------------------------------------------------------- #
# D. Cargo / UN conflict -> ASK_USER with conflict blocker, no Layer 2.
# --------------------------------------------------------------------------- #
def test_D_cargo_un_conflict_does_not_call_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-D",
                "message": "Ship perfume UN3480 from Grasse to Dubai, 500 kg.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ASK_USER"
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is False
    blocking = payload["layer1"]["intake_json"]["missing_fields"]["blocking"]
    assert "cargo / UN number conflict clarification" in blocking
    assert len(spy.calls) == 0


# --------------------------------------------------------------------------- #
# E. Multi-shipment -> ASK_USER with single-shipment blocker, no Layer 2.
# --------------------------------------------------------------------------- #
def test_E_multi_shipment_does_not_call_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-E",
                "message": (
                    "I have two shipments: lithium batteries from Shenzhen to Lyon "
                    "and textile from Milan to Paris."
                ),
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ASK_USER"
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is False
    blocking = payload["layer1"]["intake_json"]["missing_fields"]["blocking"]
    assert "single shipment selection" in blocking
    assert len(spy.calls) == 0


# --------------------------------------------------------------------------- #
# F. Ready general cargo -> SHOW_FACT_PACKAGE, Layer 2 called once with the
#    structured request (not assistant text).
# --------------------------------------------------------------------------- #
def test_F_ready_general_cargo_calls_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-F",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "SHOW_FACT_PACKAGE"
    assert payload["layer2"] is not None
    assert payload["debug"]["layer2_ran"] is True
    assert payload["layer1"]["ready_for_layer_2"] is True

    assert len(spy.calls) == 1
    sent = spy.calls[0]
    assert isinstance(sent, ValidatedShipmentRequest)
    assert sent.core_shipment.cargo_description == "textile"
    # Assistant text must never be passed to Layer 2.
    assert not hasattr(sent, "assistant_message")


# --------------------------------------------------------------------------- #
# G. Ready lithium shipment over two turns -> SHOW_FACT_PACKAGE with DG profile.
# --------------------------------------------------------------------------- #
def test_G_ready_lithium_two_turns_calls_layer2(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=True)

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-G",
                "message": "I need to ship lithium batteries from China to France.",
            },
        )
        case_id = first.json()["case_id"]
        second = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-G",
                "case_id": case_id,
                "message": "It is UN3480, 8000 kg, from Shenzhen to Lyon.",
            },
        )

    assert first.json()["next_action"] == "ASK_USER"
    assert len(spy.calls) == 1  # only the second (ready) turn reaches Layer 2

    payload = second.json()
    assert second.status_code == 200
    assert payload["next_action"] == "SHOW_FACT_PACKAGE"
    assert payload["layer2"] is not None
    assert payload["debug"]["layer2_ran"] is True

    sent = spy.calls[0]
    assert "dangerous_goods" in sent.active_profiles
    assert "lithium_battery" in sent.active_profiles
    assert sent.profiles["dangerous_goods"]["un_number"] == "UN3480"


# --------------------------------------------------------------------------- #
# H. Layer 2 exception -> controlled ERROR response, no traceback leak.
# --------------------------------------------------------------------------- #
def test_H_layer2_exception_returns_controlled_error(monkeypatch, real_layer1):
    def boom(_request):
        raise RuntimeError("fact package unavailable")

    monkeypatch.setattr(orchestrator, "build_fact_package_for_request", boom)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-H",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["next_action"] == "ERROR"
    assert payload["layer2"] is None
    assert payload["debug"]["layer2_ran"] is True
    assert payload["debug"]["error"] == "RuntimeError: fact package unavailable"
    assert "Traceback" not in (payload["debug"]["error"] or "")


# --------------------------------------------------------------------------- #
# I. FactPackage is facts-only: no final recommendation / decision fields.
# --------------------------------------------------------------------------- #
def test_I_fact_package_is_facts_only(monkeypatch, real_layer1):
    _install_layer2_spy(monkeypatch, call_through=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-I",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    payload = response.json()
    assert payload["next_action"] == "SHOW_FACT_PACKAGE"
    layer2 = payload["layer2"]

    # Facts / unknowns / provenance present.
    for fact_field in ("block_responses", "global_unknowns", "global_missing_fields", "derived_rollup"):
        assert fact_field in layer2

    # No Layer 3 style recommendation/approval fields at the Layer 2 boundary.
    forbidden = (
        "final_decision",
        "route_recommendation",
        "recommendation",
        "approved",
        "compliant",
        "booking_confirmed",
        "carrier_accepted",
    )
    for field in forbidden:
        assert field not in layer2


# --------------------------------------------------------------------------- #
# J. Layer 2 receives the structured request only, with machine fields intact.
# --------------------------------------------------------------------------- #
def test_J_layer2_receives_structured_request_only(monkeypatch, real_layer1):
    spy = _install_layer2_spy(monkeypatch, call_through=True)

    with TestClient(app) as client:
        client.post(
            "/api/v1/cortex/message",
            json={
                "conversation_id": "seam-J",
                "message": "Ship 500 kg textile from Milan to Paris by road.",
            },
        )

    assert len(spy.calls) == 1
    sent = spy.calls[0]
    assert isinstance(sent, ValidatedShipmentRequest)

    # No user-facing / assistant text leaks into the Layer 2 input contract.
    assert not hasattr(sent, "assistant_message")
    dumped = sent.model_dump()
    assert "assistant_message" not in dumped

    # Machine fields intact.
    assert sent.core_shipment.cargo_description == "textile"
    assert sent.core_shipment.weight_kg == 500
    assert sent.lane.origin_city == "Milan"
    assert sent.lane.destination_city == "Paris"
    assert sent.mode.requested_mode == RequestedMode.road
    assert sent.cargo_flags is not None
    assert isinstance(sent.profiles, dict)
