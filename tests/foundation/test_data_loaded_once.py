"""S1: the catalogue and the data assets are read from disk once per process."""

from __future__ import annotations

import builtins
import io
import json
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.data_paths import data_root
from app.services.layer2 import data_catalog
from app.services.layer2.fetch_executor import execute_fetch_plan
from app.services.layer2.fetch_planner import build_fetch_plan
from app.services.layer2.registry import BLOCK_REGISTRY
from app.services.layer2.service import build_fact_package_for_request


class _DiskAccess(AssertionError):
    """Raised by the patched ``open``. Not an OSError on purpose: connectors
    swallow OSError and answer "unknown", which would hide a disk read."""


@pytest.fixture
def count_data_opens(monkeypatch):
    """Count every ``open`` of a JSON file (``Path.open`` goes through ``io.open``).

    With ``state["forbid"]`` set, opening a file under the data root fails the test.
    """
    real_open = io.open
    opened: Counter[str] = Counter()
    state = {"forbid": False}

    def _open(file, *args, **kwargs):
        try:
            path = Path(file).resolve()
        except TypeError:  # file descriptors
            return real_open(file, *args, **kwargs)
        if path.suffix == ".json":
            opened[str(path)] += 1
            if state["forbid"] and data_root() in path.parents:
                raise _DiskAccess(f"data file read from disk after init: {path}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", _open)
    monkeypatch.setattr(builtins, "open", _open)
    return opened, state


def _dump(package) -> dict:
    return json.loads(package.model_dump_json())


def test_init_reads_every_file_exactly_once(fresh_data_store, count_data_opens):
    opened, _ = count_data_opens

    loaded = data_catalog.init_data()

    assert len(loaded.catalog) > 100
    assert len(opened) == len(loaded.catalog)
    assert set(opened.values()) == {1}


def test_second_init_does_not_read_again(fresh_data_store, count_data_opens):
    opened, _ = count_data_opens
    first = data_catalog.init_data()
    opened.clear()

    second = data_catalog.init_data()

    assert second is first
    assert not opened


def test_connector_calls_do_not_hit_the_disk_after_init(
    fresh_data_store, count_data_opens, sample_requests, monkeypatch
):
    opened, state = count_data_opens
    data_catalog.init_data()
    expected = [_dump(build_fact_package_for_request(request)) for request in sample_requests()]

    # From here on any read of a data file, or any catalogue rebuild, is a failure.
    # Connector caches are emptied so every connector has to go back to the store.
    data_catalog.clear_connector_caches()
    opened.clear()
    state["forbid"] = True

    def _no_rebuild(*_args, **_kwargs):
        raise _DiskAccess("the data catalogue was rebuilt after init")

    monkeypatch.setattr(data_catalog, "build_data_catalog", _no_rebuild)
    monkeypatch.setattr(data_catalog, "_load", _no_rebuild)

    called_blocks: set[str] = set()
    actual = []
    for request in sample_requests():
        plan = build_fetch_plan(request)
        called_blocks.update(item.block_id for item in plan.items)
        execute_fetch_plan(request, plan)
        actual.append(_dump(build_fact_package_for_request(request)))
    # ...and each registered connector directly, whatever the planner selected.
    for block_id, connector in BLOCK_REGISTRY.items():
        for request in sample_requests():
            connector(request)
        called_blocks.add(block_id)

    assert not opened
    assert called_blocks == set(BLOCK_REGISTRY)
    assert actual == expected


def test_every_connector_declared_file_is_loaded_at_init(fresh_data_store):
    loaded = data_catalog.init_data()
    declared = data_catalog.connector_declared_paths()

    assert len(declared) == 31
    assert set(declared) <= set(loaded.payloads)
    assert set(declared) <= loaded.required_paths
    # Each block of the registry that has a main dataset has it loaded too.
    for block_id in BLOCK_REGISTRY:
        asset = data_catalog.get_main_asset(block_id)
        if asset is not None:
            assert asset.path in loaded.payloads, block_id


def test_main_asset_lookup_uses_the_loaded_catalogue(fresh_data_store, count_data_opens):
    opened, state = count_data_opens
    data_catalog.init_data()
    opened.clear()
    state["forbid"] = True

    for block_id in BLOCK_REGISTRY:
        data_catalog.get_main_asset(block_id)
    sea_main = data_catalog.find_assets(mode="sea", role="main")

    assert sea_main
    assert not opened


def test_loaded_asset_is_shared_not_reparsed(fresh_data_store):
    data_catalog.init_data()
    path = Path("data/sea/sea_c_port_capability.json")

    assert data_catalog.load_json_asset(path) is data_catalog.load_json_asset(path)
    assert data_catalog.load_json_asset(path) is data_catalog.load_json_asset(str(path))


def test_connectors_do_not_modify_the_shared_data(fresh_data_store, sample_requests):
    """The loaded payloads are shared by every request: they must stay as loaded."""
    loaded = data_catalog.init_data()
    before = {key: json.dumps(value, sort_keys=True) for key, value in loaded.payloads.items()}

    for _ in range(2):
        for request in sample_requests():
            build_fact_package_for_request(request)
        for connector in BLOCK_REGISTRY.values():
            for request in sample_requests():
                connector(request)

    after = {key: json.dumps(value, sort_keys=True) for key, value in loaded.payloads.items()}
    assert after.keys() == before.keys()
    changed = [key for key in before if before[key] != after[key]]
    assert changed == []


def test_undeclared_file_is_read_once_then_served_from_memory(
    fresh_data_store, count_data_opens, tmp_path
):
    opened, _ = count_data_opens
    data_catalog.init_data()
    extra = tmp_path / "extra_asset.json"
    extra.write_text('{"records": [1, 2, 3]}', encoding="utf-8")
    opened.clear()

    first = data_catalog.load_json_asset(extra)
    second = data_catalog.load_json_asset(extra)

    assert first == {"records": [1, 2, 3]}
    assert second is first
    assert opened[str(extra.resolve())] == 1


def test_unknown_file_raises_so_the_connector_reports_unknown(fresh_data_store):
    data_catalog.init_data()

    with pytest.raises(OSError):
        data_catalog.load_json_asset("data/sea/does_not_exist.json")


def test_api_startup_loads_the_data(fresh_data_store):
    from app.main import app

    assert not data_catalog.is_data_loaded()
    with TestClient(app):
        assert data_catalog.is_data_loaded()
