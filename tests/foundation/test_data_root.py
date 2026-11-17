"""S2: absolute data root, independent of the working directory, failing loudly."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import readiness
from app.core.data_paths import (
    DATA_DIR_ENV,
    PACKAGE_DATA_ROOT,
    data_root,
    logical_data_path,
    resolve_data_path,
)
from app.schemas import BlockStatus
from app.services.layer2 import data_catalog
from app.services.layer2.data_catalog import DataLoadError
from app.services.layer2.service import build_fact_package_for_request
from tests.prompt_budget_helpers import multimode_request

REPO_ROOT = Path(__file__).resolve().parents[2]

_BUILD_SCRIPT = """
import json
from tests.prompt_budget_helpers import multimode_request
from app.services.layer2.service import build_fact_package_for_request

package = build_fact_package_for_request(multimode_request())
print(json.dumps(json.loads(package.model_dump_json()), sort_keys=True))
"""


def _build_dump() -> dict:
    package = build_fact_package_for_request(multimode_request())
    return json.loads(package.model_dump_json())


def _found_blocks(dump: dict) -> list[str]:
    return [
        block["block_id"]
        for block in dump["block_responses"]
        if block["status"] == BlockStatus.found.value
    ]


# --------------------------------------------------------------------------- #
# data root resolution
# --------------------------------------------------------------------------- #
def test_default_data_root_is_absolute_and_comes_from_the_package(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)

    before = data_root()
    monkeypatch.chdir(tmp_path)
    after = data_root()

    assert before.is_absolute()
    assert before == after == PACKAGE_DATA_ROOT == REPO_ROOT / "data"


def test_cortex_data_dir_env_overrides_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))

    assert data_root() == tmp_path.resolve()


def test_logical_paths_resolve_under_the_root(tmp_path):
    logical = "data/sea/sea_c_port_capability.json"

    assert resolve_data_path(logical, tmp_path) == tmp_path / "sea" / "sea_c_port_capability.json"
    assert logical_data_path(tmp_path / "sea" / "sea_c_port_capability.json", tmp_path) == logical
    assert logical_data_path(Path(logical), tmp_path) == logical


# --------------------------------------------------------------------------- #
# same Layer 2 result from another working directory
# --------------------------------------------------------------------------- #
def test_layer2_build_is_the_same_from_another_cwd(monkeypatch, tmp_path, fresh_data_store):
    from_repo = _build_dump()

    data_catalog.reset_data()  # drop everything cached, then build again elsewhere
    monkeypatch.chdir(tmp_path)
    from_elsewhere = _build_dump()

    assert from_elsewhere == from_repo
    # Guard against "same" meaning "both empty": the build really found data.
    assert len(_found_blocks(from_elsewhere)) >= 5


def test_layer2_build_is_the_same_in_a_process_started_elsewhere(tmp_path):
    """A fresh interpreter started outside the repo gives the repo's result."""
    env = {key: value for key, value in os.environ.items() if key != DATA_DIR_ENV}
    env["PYTHONPATH"] = str(REPO_ROOT)

    def run(cwd: Path) -> dict:
        completed = subprocess.run(
            [sys.executable, "-c", _BUILD_SCRIPT],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        return json.loads(completed.stdout.strip().splitlines()[-1])

    from_repo = run(REPO_ROOT)
    from_elsewhere = run(tmp_path)

    assert from_elsewhere == from_repo
    assert len(_found_blocks(from_elsewhere)) >= 5


def test_relocated_data_folder_gives_the_same_result(monkeypatch, data_copy, fresh_data_store):
    from_default = _build_dump()

    monkeypatch.setenv(DATA_DIR_ENV, str(data_copy))
    data_catalog.reset_data()
    loaded = data_catalog.init_data()

    assert loaded.root == data_copy.resolve()
    assert _build_dump() == from_default


# --------------------------------------------------------------------------- #
# missing or broken assets fail loudly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "relative_path",
    [
        # main dataset read by its connector
        "sea/sea_c_port_capability.json",
        "air/cortex_air_block_a_dg_records_REPAIRED.json",
        "road/road_c_corridor_viability.json",
        # main dataset of a registry block whose connector does not read it yet (SEA-F)
        "sea/sea_f_maritime_documents_border_gates.json",
        # supporting file declared by a connector
        "sea/sea_b_container_specs.json",
        # AIR-COST reads a file the catalogue classes as "support"
        "air/air_reference.json",
    ],
)
def test_missing_required_asset_fails_loudly(relative_path, data_copy, fresh_data_store):
    (data_copy / relative_path).unlink()

    with pytest.raises(DataLoadError) as excinfo:
        data_catalog.init_data(data_copy)

    message = str(excinfo.value)
    assert str(data_copy.resolve() / relative_path) in message
    assert "is missing" in message
    assert not data_catalog.is_data_loaded()
    assert data_catalog.data_load_error() == message


def test_invalid_json_asset_fails_loudly(data_copy, fresh_data_store):
    broken = data_copy / "road" / "road_cost_reference.json"
    broken.unlink()
    broken.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(DataLoadError) as excinfo:
        data_catalog.init_data(data_copy)

    message = str(excinfo.value)
    assert str(broken.resolve()) in message
    assert "is not valid JSON" in message
    assert not data_catalog.is_data_loaded()


def test_every_problem_is_named_in_one_error(data_copy, fresh_data_store):
    first = data_copy / "sea" / "sea_d_carrier_trade_lane_reference.json"
    second = data_copy / "air" / "cortex_air_block_h_dataset.json"
    first.unlink()
    second.unlink()

    with pytest.raises(DataLoadError) as excinfo:
        data_catalog.init_data(data_copy)

    assert first.name in str(excinfo.value)
    assert second.name in str(excinfo.value)


def test_missing_data_root_fails_loudly(tmp_path, fresh_data_store):
    missing_root = tmp_path / "no-such-data-folder"

    with pytest.raises(DataLoadError) as excinfo:
        data_catalog.init_data(missing_root)

    assert str(missing_root) in str(excinfo.value)
    assert DATA_DIR_ENV in str(excinfo.value)


def test_wrong_data_folder_is_an_error_not_a_silent_all_unknown(
    monkeypatch, tmp_path, fresh_data_store
):
    """S2 itself: pointing at the wrong folder must not produce an "unknown" package."""
    wrong = tmp_path / "wrong-folder"
    wrong.mkdir()
    monkeypatch.setenv(DATA_DIR_ENV, str(wrong))

    with pytest.raises(DataLoadError):
        build_fact_package_for_request(multimode_request())
    with pytest.raises(DataLoadError):
        data_catalog.get_main_asset("SEA-C")


def test_unneeded_file_may_be_missing(data_copy, fresh_data_store):
    """Only required assets stop the load; files no connector uses do not."""
    (data_copy / "sea" / "sea_a_metadata.json").unlink()

    loaded = data_catalog.init_data(data_copy)

    assert "data/sea/sea_a_metadata.json" not in loaded.required_paths
    assert data_catalog.is_data_loaded()


# --------------------------------------------------------------------------- #
# the API refuses to start, and /ready says not ready
# --------------------------------------------------------------------------- #
def test_api_refuses_to_start_when_a_main_asset_is_missing(
    monkeypatch, data_copy, fresh_data_store
):
    from app.main import app

    missing = data_copy / "sea" / "sea_c_port_capability.json"
    missing.unlink()
    monkeypatch.setenv(DATA_DIR_ENV, str(data_copy))

    with pytest.raises(DataLoadError) as excinfo:
        with TestClient(app):
            pass  # pragma: no cover - startup must fail before this line

    assert str(missing.resolve()) in str(excinfo.value)


def test_ready_is_not_ready_when_data_failed_to_load(monkeypatch, data_copy, fresh_data_store):
    from app.main import app

    async def _ok() -> None:
        return None

    monkeypatch.setattr(readiness, "check_database", _ok)
    monkeypatch.setattr(readiness, "check_redis", _ok)
    (data_copy / "sea" / "sea_c_port_capability.json").unlink()
    with pytest.raises(DataLoadError):
        data_catalog.init_data(data_copy)

    # No lifespan here: we ask the running app object directly.
    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"
    assert body["checks"]["data"] != "ok"
