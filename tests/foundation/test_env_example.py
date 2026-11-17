"""S4 + S6: tracing is off by default and .env.example mirrors the real settings."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app.config import Settings
from app.core import tracing
from app.core.auth import API_KEYS_ENV
from app.core.data_paths import DATA_DIR_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

_ACTIVE_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_COMMENTED_RE = re.compile(r"^#\s*([A-Z][A-Z0-9_]*)=(.*)$")

# Environment names read outside app/config.py.
_OTHER_ENV_NAMES = {
    "LAYER2_PROVIDER",  # app/services/layer2/provider_config.py
    "LAYER2_PROVIDER_OVERRIDES",
    "POSTGRES_HOST_PORT",  # docker-compose.yml
    "REDIS_HOST_PORT",
}


def _example_entries() -> tuple[dict[str, str], dict[str, str]]:
    """Return (active NAME=value lines, commented-out "# NAME=value" lines)."""
    active: dict[str, str] = {}
    commented: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if match := _ACTIVE_RE.match(stripped):
            active[match.group(1)] = match.group(2)
        elif match := _COMMENTED_RE.match(stripped):
            commented[match.group(1)] = match.group(2)
    return active, commented


def _defaults(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings as the code defines them: no .env file, no environment override."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(_env_file=None)


# --------------------------------------------------------------------------- #
# S4: LangSmith tracing off by default
# --------------------------------------------------------------------------- #
def test_langsmith_tracing_defaults_to_false(monkeypatch):
    assert Settings.model_fields["langsmith_tracing"].default is False
    assert _defaults(monkeypatch).langsmith_tracing is False


def test_env_example_keeps_tracing_off_and_says_why():
    active, _ = _example_entries()
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert active["LANGSMITH_TRACING"] == "false"
    assert active["LANGSMITH_API_KEY"] == ""
    assert "OFF by default" in text
    assert "third party" in text


def test_tracing_off_disables_the_langchain_tracer(monkeypatch):
    """With the default, startup forces the tracing switches off for the process."""
    monkeypatch.setattr(tracing.settings, "langsmith_tracing", False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    assert tracing.init_langsmith() is None
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"


# --------------------------------------------------------------------------- #
# S6: .env.example mirrors every real setting name, with no secret values
# --------------------------------------------------------------------------- #
def test_env_example_lists_every_setting():
    active, commented = _example_entries()
    listed = set(active) | set(commented)
    expected = {name.upper() for name in Settings.model_fields} | _OTHER_ENV_NAMES

    assert expected - listed == set()


def test_env_example_lists_the_per_layer_model_settings():
    active, _ = _example_entries()

    for name in (
        "LLM_INTAKE_PROVIDER",
        "LLM_LAYER3_PROVIDER",
        "LLM_LAYER4_PROVIDER",
        "GOOGLE_AI_INTAKE_MODEL",
        "GOOGLE_AI_LAYER3_MODEL",
        "GOOGLE_AI_LAYER4_MODEL",
        "OLLAMA_INTAKE_MODEL",
        "OLLAMA_LAYER3_MODEL",
        "OLLAMA_LAYER4_MODEL",
    ):
        assert name in active, name


def test_env_example_has_no_unknown_setting():
    """A name in the example that nothing reads is drift in the other direction."""
    active, commented = _example_entries()
    known = {name.upper() for name in Settings.model_fields} | _OTHER_ENV_NAMES
    # Read by the Google and LangSmith client libraries, not by app/config.py.
    known |= {"GOOGLE_API_KEY", "GEMINI_API_KEY", "LANGSMITH_ENDPOINT"}

    assert (set(active) | set(commented)) - known == set()


def test_env_example_holds_no_secret_values():
    active, commented = _example_entries()

    for name, value in {**commented, **active}.items():
        if name.endswith("_API_KEY"):
            assert value in {"", "PASTE_YOUR_REAL_KEY_HERE"}, name
    assert active[API_KEYS_ENV] == ""
    assert active[DATA_DIR_ENV] == ""


def test_env_example_ports_do_not_clash_with_the_usual_local_services(monkeypatch):
    active, _ = _example_entries()

    assert active["API_PORT"] == "8100"  # the legacy Backend keeps 8000
    assert active["POSTGRES_HOST_PORT"] == "5433"
    assert active["REDIS_HOST_PORT"] == "6380"
    assert _defaults(monkeypatch).api_port == 8100


def test_docker_compose_defaults_match_the_documented_ports():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"${API_PORT:-8100}:${API_PORT:-8100}"' in compose
    assert '"${POSTGRES_HOST_PORT:-5433}:5432"' in compose
    assert '"${REDIS_HOST_PORT:-6380}:6379"' in compose
    for clashing in ('"5432:5432"', '"6379:6379"', ":-8000", "'8000'"):
        assert clashing not in compose


def test_container_start_checks_and_migrates_before_uvicorn():
    entrypoint = (REPO_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    commands = [
        line.strip()
        for line in entrypoint.splitlines()
        if line.strip() and not line.strip().startswith(("#", "echo "))
    ]

    # Any failing step stops the container; the order is check, migrate, serve.
    assert commands[0] == "set -eu"
    assert commands[1] == "python -m app.core.preflight"
    assert commands[2] == "alembic upgrade head"
    assert commands[3].startswith("exec uvicorn app.main:app")
    assert "/app/docker/entrypoint.sh" in dockerfile
