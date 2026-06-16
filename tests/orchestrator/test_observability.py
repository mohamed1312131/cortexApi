"""Observability + freeze-cleanup tests.

Covers structured-logging safety, trace_id propagation, Redis-fallback
visibility, and confirmation that removed dead code stays removed. Layer 1 is
faked (no LLM) so these stay deterministic and offline.
"""

from __future__ import annotations

import importlib
import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    CaseAction,
    CaseState,
    IntakeDecision,
    IntakeIntent,
    IntakeResult,
    MissingFields,
    QuestionToUser,
    ValidatedShipmentRequest,
)


def _fake_intake_result(*, conversation_id, case_id="case-obs", ready=False) -> IntakeResult:
    return IntakeResult(
        conversation_id=conversation_id,
        case_id=case_id,
        case_action=CaseAction.create_new_case,
        intent=IntakeIntent.shipment_readiness,
        decision=IntakeDecision.ready_for_layer_2 if ready else IntakeDecision.ask_user,
        assistant_message="ok",
        ready_for_layer_2=ready,
        intake_json=ValidatedShipmentRequest(
            case_id=case_id,
            missing_fields=MissingFields(blocking=[] if ready else ["weight or quantity"]),
        ),
        questions_to_user=[]
        if ready
        else [
            QuestionToUser(
                question="What is the weight?",
                reason="Needed.",
                field_target="core_shipment.weight_kg",
            )
        ],
    )


def _patch_layer1(monkeypatch, *, ready=False):
    def fake(**kwargs):
        return _fake_intake_result(conversation_id=kwargs.get("conversation_id"), ready=ready)

    monkeypatch.setattr(
        "app.services.orchestrator.cortex_orchestrator.handle_intake_message", fake
    )
    monkeypatch.setattr("app.api.v1.routes_intake.handle_intake_message", fake)


# --- trace_id ------------------------------------------------------------- #
def test_cortex_debug_trace_id_populated(monkeypatch):
    _patch_layer1(monkeypatch, ready=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/cortex/message",
            json={"conversation_id": "obs-trace", "message": "x"},
        )
    payload = response.json()
    assert response.status_code == 200
    assert payload["debug"]["trace_id"]  # non-empty string
    # Distinct requests get distinct trace ids.
    with TestClient(app) as client:
        second = client.post(
            "/api/v1/cortex/message",
            json={"conversation_id": "obs-trace", "message": "y"},
        ).json()
    assert second["debug"]["trace_id"] != payload["debug"]["trace_id"]


# --- logging does not break endpoints ------------------------------------- #
def test_endpoints_do_not_crash_with_logging(monkeypatch, caplog):
    _patch_layer1(monkeypatch, ready=False)
    with caplog.at_level(logging.INFO, logger="cortex"):
        with TestClient(app) as client:
            cortex = client.post(
                "/api/v1/cortex/message",
                json={"conversation_id": "obs-log", "message": "x"},
            )
            intake = client.post(
                "/api/v1/intake/message",
                json={"conversation_id": "obs-log", "message": "x"},
            )
    assert cortex.status_code == 200
    assert intake.status_code == 200
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "cortex.received" in messages
    assert "intake.received" in messages
    assert "layer1.outcome" in messages
    assert "orchestrator.gate" in messages


# --- Redis fallback visibility -------------------------------------------- #
def test_redis_fallback_logs_warning(caplog):
    from redis import RedisError

    from app.services.layer1.case_state_manager import RedisCaseStateStore

    store = RedisCaseStateStore("redis://localhost:6390/0")

    class _Boom:
        def get(self, *args, **kwargs):
            raise RedisError("connection refused")

        def setex(self, *args, **kwargs):
            raise RedisError("connection refused")

    store._redis = _Boom()

    with caplog.at_level(logging.WARNING, logger="cortex"):
        store.get("CASE-OBS")
        store.save(CaseState(case_id="CASE-OBS", conversation_id="conv-obs"))

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("case_state.redis_fallback" in m for m in warnings)
    assert any("op=save" in m for m in warnings)


# --- dead code stays removed ---------------------------------------------- #
def test_removed_redis_client_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.core.redis_client")


def test_removed_dead_symbols():
    # The old multi-node Layer 1 pipeline (state, nodes, extractors, validators)
    # was hard-deleted when Layer 1 became a single agent turn.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.layer1.state")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.layer1.deterministic_update_extractor")
    data_catalog = importlib.import_module("app.services.layer2.data_catalog")
    assert not hasattr(data_catalog, "summarize_catalog")
