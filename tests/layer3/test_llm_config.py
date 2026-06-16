from __future__ import annotations

import sys
from types import SimpleNamespace

from app.config import Settings, iter_model_config_debug_values
from app.core import llm
from app.services.layer3.agents import analyst_agent, critic_agent


def test_settings_reads_google_ai_layer3_model(monkeypatch):
    monkeypatch.setenv("GOOGLE_AI_LAYER3_MODEL", "gemma-layer3")

    fresh = Settings(_env_file=None)

    assert fresh.google_ai_layer3_model == "gemma-layer3"


def test_google_layer3_model_selection_uses_layer3_model(monkeypatch):
    monkeypatch.setattr(llm.settings, "google_ai_model", "gemma-base")
    monkeypatch.setattr(llm.settings, "google_ai_layer3_model", "gemma-layer3")

    assert llm.get_google_model_name(layer3=True) == "gemma-layer3"


def test_google_layer3_model_selection_falls_back_to_base(monkeypatch):
    monkeypatch.setattr(llm.settings, "google_ai_model", "gemma-base")
    monkeypatch.setattr(llm.settings, "google_ai_layer3_model", " ")

    assert llm.get_google_model_name(layer3=True) == "gemma-base"


def test_google_intake_model_selection_still_prefers_intake(monkeypatch):
    monkeypatch.setattr(llm.settings, "google_ai_model", "gemma-base")
    monkeypatch.setattr(llm.settings, "google_ai_intake_model", "gemma-intake")
    monkeypatch.setattr(llm.settings, "google_ai_layer3_model", "gemma-layer3")

    assert llm.get_google_model_name(intake=True) == "gemma-intake"


def test_get_chat_model_passes_layer3_bare_model_name(monkeypatch):
    class _FakeGoogleModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setitem(
        sys.modules,
        "langchain_google_genai",
        SimpleNamespace(ChatGoogleGenerativeAI=_FakeGoogleModel),
    )
    monkeypatch.setattr(llm.settings, "llm_provider", "google")
    monkeypatch.setattr(llm.settings, "google_ai_api_key", "VALUE_A")
    monkeypatch.setattr(llm.settings, "google_ai_model", "gemma-base")
    monkeypatch.setattr(llm.settings, "google_ai_layer3_model", "gemma-layer3")

    model = llm.get_chat_model(layer3=True)

    assert model.kwargs["model"] == "gemma-layer3"
    assert not model.kwargs["model"].startswith("models/")


def test_layer3_agents_request_layer3_model(monkeypatch):
    captured: list[dict] = []
    sentinel = object()

    def _fake_get_chat_model(**kwargs):
        captured.append(kwargs)
        return sentinel

    monkeypatch.setattr(analyst_agent, "get_chat_model", _fake_get_chat_model)
    monkeypatch.setattr(critic_agent, "get_chat_model", _fake_get_chat_model)

    assert analyst_agent._require_model(None) is sentinel
    assert critic_agent._require_model(None) is sentinel
    assert captured == [{"layer3": True}, {"layer3": True}]


def test_model_config_debug_values_redact_keys():
    fresh = Settings(
        _env_file=None,
        llm_api_key="VALUE_A",
        google_ai_api_key="VALUE_B",
        google_ai_model="gemma-base",
        google_ai_layer3_model="gemma-layer3",
    )

    output = "\n".join(
        f"{name} = {value}" for name, value in iter_model_config_debug_values(fresh)
    )

    assert "VALUE_A" not in output
    assert "VALUE_B" not in output
    assert "llm_api_key = ***REDACTED***" in output
    assert "google_ai_api_key = ***REDACTED***" in output
