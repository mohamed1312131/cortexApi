"""The startup preflight stops a bad start with one clear error (S2)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.core import preflight
from app.core.data_paths import DATA_DIR_ENV
from app.services.layer2 import data_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_preflight_passes_on_the_repository_data(fresh_data_store, capsys):
    assert preflight.main() == 0

    assert "preflight ok" in capsys.readouterr().out
    assert data_catalog.is_data_loaded()


def test_preflight_fails_and_names_the_missing_file(
    monkeypatch, data_copy, fresh_data_store, capsys
):
    missing = data_copy / "sea" / "sea_c_port_capability.json"
    missing.unlink()
    monkeypatch.setenv(DATA_DIR_ENV, str(data_copy))

    assert preflight.main() == 1

    captured = capsys.readouterr()
    assert "preflight failed: DataLoadError" in captured.err
    assert str(missing.resolve()) in captured.err
    assert "preflight ok" not in captured.out
    assert not data_catalog.is_data_loaded()


def test_preflight_fails_without_api_keys_outside_local(monkeypatch, fresh_data_store, capsys):
    monkeypatch.setattr(settings, "cortex_api_keys_json", "")
    monkeypatch.setattr(settings, "app_env", "production")

    assert preflight.main() == 1

    assert "preflight failed: ApiKeyConfigError" in capsys.readouterr().err


def test_preflight_command_exits_non_zero_on_a_wrong_data_folder(tmp_path):
    """What the container entrypoint runs: ``python -m app.core.preflight``."""
    wrong = tmp_path / "wrong-folder"
    wrong.mkdir()
    env = dict(os.environ)
    env[DATA_DIR_ENV] = str(wrong)
    env["PYTHONPATH"] = str(REPO_ROOT)

    completed = subprocess.run(
        [sys.executable, "-m", "app.core.preflight"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 1
    assert "preflight failed: DataLoadError" in completed.stderr
    assert str(wrong) in completed.stderr
    assert "is missing" in completed.stderr
