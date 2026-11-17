"""W0-FU step 2: the settings file is found from the code location.

Rule: ``<repository>/.env`` (next to ``app/``) is read when it exists, whatever
the working directory is. It is optional: when it does not exist the settings
come from the process environment and the code defaults (the Docker image has no
``.env``).

The behaviour is checked in fresh interpreters on a throwaway copy of
``app/config.py`` placed next to a synthetic ``.env``: the developer's real
``.env`` is never used here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.config import ENV_FILE, PACKAGE_ROOT, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

_PRINT_SETTINGS = """
import json
from app import config
from app.config import settings

print(json.dumps({
    "env_file": str(config.ENV_FILE),
    "app_env": settings.app_env,
    "api_port": settings.api_port,
    "llm_provider": settings.llm_provider,
}))
"""

_SYNTHETIC_ENV = "APP_ENV=staging\nAPI_PORT=8123\nLLM_PROVIDER=ollama\n"
_DECOY_ENV = "APP_ENV=decoy\nAPI_PORT=9999\nLLM_PROVIDER=openai\n"


@pytest.fixture
def package_copy(tmp_path: Path) -> Path:
    """A package root that holds only a copy of app/config.py."""
    root = tmp_path / "package-root"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(REPO_ROOT / "app" / "config.py", root / "app" / "config.py")
    return root


def _settings_seen_from(cwd: Path, package_root: Path, **extra_env: str) -> dict:
    setting_names = {name.upper() for name in Settings.model_fields}
    env = {key: value for key, value in os.environ.items() if key.upper() not in setting_names}
    env["PYTHONPATH"] = str(package_root)
    env.update(extra_env)
    completed = subprocess.run(
        [sys.executable, "-c", _PRINT_SETTINGS],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# where the file is looked for
# --------------------------------------------------------------------------- #
def test_env_file_path_is_absolute_and_comes_from_the_package(monkeypatch, tmp_path):
    before = config.ENV_FILE
    monkeypatch.chdir(tmp_path)

    assert before.is_absolute()
    assert before == config.ENV_FILE == ENV_FILE == REPO_ROOT / ".env"
    assert PACKAGE_ROOT == REPO_ROOT


def test_settings_class_reads_that_file_and_no_relative_path(package_copy):
    """In a fresh interpreter (no test pin): model_config points at the absolute path."""
    script = (
        "from app import config; "
        "print(config.Settings.model_config['env_file'] == config.ENV_FILE, "
        "config.ENV_FILE.is_absolute())"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=package_copy.parent,
        env={**os.environ, "PYTHONPATH": str(package_copy)},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.split() == ["True", "True"]


# --------------------------------------------------------------------------- #
# same settings from any working directory
# --------------------------------------------------------------------------- #
def test_settings_are_the_same_from_another_working_directory(package_copy, tmp_path):
    (package_copy / ".env").write_text(_SYNTHETIC_ENV, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    from_package_root = _settings_seen_from(package_copy, package_copy)
    from_elsewhere = _settings_seen_from(elsewhere, package_copy)

    assert from_elsewhere == from_package_root
    assert from_elsewhere == {
        "env_file": str((package_copy / ".env").resolve()),
        "app_env": "staging",
        "api_port": 8123,
        "llm_provider": "ollama",
    }


def test_env_file_in_the_working_directory_is_not_read(package_copy, tmp_path):
    """Before W0-FU the working directory decided: a .env there was the one read."""
    (package_copy / ".env").write_text(_SYNTHETIC_ENV, encoding="utf-8")
    decoy_dir = tmp_path / "folder-with-another-env"
    decoy_dir.mkdir()
    (decoy_dir / ".env").write_text(_DECOY_ENV, encoding="utf-8")

    seen = _settings_seen_from(decoy_dir, package_copy)

    assert seen["app_env"] == "staging"
    assert seen["api_port"] == 8123
    assert seen["llm_provider"] == "ollama"


def test_environment_variables_still_win_over_the_file(package_copy, tmp_path):
    (package_copy / ".env").write_text(_SYNTHETIC_ENV, encoding="utf-8")

    seen = _settings_seen_from(tmp_path, package_copy, API_PORT="8456", APP_ENV="production")

    assert seen["api_port"] == 8456
    assert seen["app_env"] == "production"
    assert seen["llm_provider"] == "ollama"  # not overridden: still from the file


# --------------------------------------------------------------------------- #
# no file at all (Docker): environment only
# --------------------------------------------------------------------------- #
def test_missing_env_file_is_allowed_and_the_environment_is_used(package_copy, tmp_path):
    """The Docker image has no .env: compose passes the environment."""
    assert not (package_copy / ".env").exists()

    seen = _settings_seen_from(tmp_path, package_copy, APP_ENV="staging", API_PORT="8222")

    assert seen["app_env"] == "staging"
    assert seen["api_port"] == 8222
