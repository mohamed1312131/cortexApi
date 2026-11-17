"""W0-FU step 4: the shared Layer 2 data is frozen.

One run can never modify what another run reads: every loaded payload is a
``FrozenDict`` / ``FrozenList`` at every depth and refuses modification, while
it still reads exactly like the plain ``dict`` / ``list`` it was parsed into.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import operator
import pickle
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app.core.data_paths import resolve_data_path
from app.services.layer2 import data_catalog
from app.services.layer2.data_catalog import FrozenDataError, FrozenDict, FrozenList, freeze
from app.services.layer2.registry import BLOCK_REGISTRY
from app.services.layer2.service import build_fact_package_for_request

# A small asset (22 KB) with nested dicts and lists; a connector's main dataset.
SAMPLE = "data/sea/sea_d_carrier_trade_lane_reference.json"


def _walk(value: Any) -> Iterator[Any]:
    """Every container in a JSON value, at any depth."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        yield value
        for item in value:
            yield from _walk(item)


def _first(payload: Any, kind: type, *, non_empty: bool = True, skip_top: bool = True) -> Any:
    for node in _walk(payload):
        if skip_top and node is payload:
            continue
        if isinstance(node, kind) and (node or not non_empty):
            return node
    raise AssertionError(f"no nested {kind.__name__} in the payload")


def _set_item(container: Any) -> None:
    container[0 if isinstance(container, list) else "injected"] = "changed"


def _del_item(container: Any) -> None:
    del container[0 if isinstance(container, list) else next(iter(container))]


def _augmented_or(container: dict) -> None:
    container |= {"injected": "changed"}


def _augmented_add(container: list) -> None:
    container += ["injected"]


def _augmented_mul(container: list) -> None:
    container *= 2


def _slice_assign(container: list) -> None:
    container[:] = []


DICT_MUTATIONS: dict[str, Callable[[dict], Any]] = {
    "d[k] = v": _set_item,
    "del d[k]": _del_item,
    "d |= other": _augmented_or,
    "clear": lambda d: d.clear(),
    "pop": lambda d: d.pop(next(iter(d))),
    "pop missing with default": lambda d: d.pop("no-such-key", None),
    "popitem": lambda d: d.popitem(),
    "setdefault new key": lambda d: d.setdefault("injected", "changed"),
    "setdefault existing key": lambda d: d.setdefault(next(iter(d)), "changed"),
    "update": lambda d: d.update(injected="changed"),
    "update empty": lambda d: d.update({}),
    "operator.setitem": lambda d: operator.setitem(d, "injected", "changed"),
}

LIST_MUTATIONS: dict[str, Callable[[list], Any]] = {
    "l[i] = v": _set_item,
    "del l[i]": _del_item,
    "l[:] = []": _slice_assign,
    "l += other": _augmented_add,
    "l *= n": _augmented_mul,
    "append": lambda items: items.append("injected"),
    "clear": lambda items: items.clear(),
    "extend": lambda items: items.extend(["injected"]),
    "insert": lambda items: items.insert(0, "injected"),
    "pop": lambda items: items.pop(),
    "remove": lambda items: items.remove(items[0]),
    "reverse": lambda items: items.reverse(),
    "sort": lambda items: items.sort(key=repr),
}


# --------------------------------------------------------------------------- #
# everything handed out is frozen, at every depth
# --------------------------------------------------------------------------- #
def test_every_loaded_payload_is_frozen_at_every_depth(fresh_data_store):
    loaded = data_catalog.init_data()

    dicts = lists = 0
    for key, payload in loaded.payloads.items():
        for node in _walk(payload):
            if isinstance(node, dict):
                assert type(node) is FrozenDict, key
                dicts += 1
            else:
                assert type(node) is FrozenList, key
                lists += 1
    # Not vacuous: the 32 loaded assets hold a lot of containers.
    assert len(loaded.payloads) == 32
    assert dicts > 10_000
    assert lists > 10_000


@pytest.mark.parametrize("mutation", sorted(DICT_MUTATIONS))
def test_mutation_attempt_on_a_loaded_record_raises(mutation):
    payload = data_catalog.load_json_asset(SAMPLE)
    record = _first(payload, dict)
    before = json.dumps(payload, sort_keys=True)

    for target in (payload, record):
        with pytest.raises(FrozenDataError):
            DICT_MUTATIONS[mutation](target)

    assert json.dumps(payload, sort_keys=True) == before


@pytest.mark.parametrize("mutation", sorted(LIST_MUTATIONS))
def test_mutation_attempt_on_a_loaded_list_raises(mutation):
    payload = data_catalog.load_json_asset(SAMPLE)
    records = _first(payload, list)
    before = json.dumps(payload, sort_keys=True)

    with pytest.raises(FrozenDataError):
        LIST_MUTATIONS[mutation](records)

    assert json.dumps(payload, sort_keys=True) == before


def test_the_error_is_a_type_error_and_says_what_to_do():
    record = _first(data_catalog.load_json_asset(SAMPLE), dict)

    with pytest.raises(TypeError) as excinfo:
        record["injected"] = "changed"

    assert isinstance(excinfo.value, FrozenDataError)
    assert "read-only" in str(excinfo.value)
    assert "copy" in str(excinfo.value)


def test_late_loaded_asset_is_frozen_too(fresh_data_store, tmp_path):
    data_catalog.init_data()
    extra = tmp_path / "extra_asset.json"
    extra.write_text('{"records": [{"code": "A", "tags": ["x"]}]}', encoding="utf-8")

    payload = data_catalog.load_json_asset(extra)

    assert [type(node) for node in _walk(payload)] == [
        FrozenDict,
        FrozenList,
        FrozenDict,
        FrozenList,
    ]
    with pytest.raises(FrozenDataError):
        payload["records"][0]["tags"].append("y")
    # It is in the index now, served from memory, and cannot be replaced there.
    assert data_catalog.load_json_asset(extra) is payload
    loaded = data_catalog.get_loaded_data()
    late_keys = [key for key, value in loaded.payloads.items() if value is payload]
    assert len(late_keys) == 1
    with pytest.raises(FrozenDataError):
        loaded.payloads[late_keys[0]] = {}
    with pytest.raises(FrozenDataError):
        data_catalog._add_payload(loaded, late_keys[0], payload)


def test_catalogue_entries_are_read_only(fresh_data_store):
    loaded = data_catalog.init_data()
    asset = data_catalog.get_main_asset("SEA-C")
    assert asset is not None

    with pytest.raises(dataclasses.FrozenInstanceError):
        asset.path = "data/sea/other.json"  # type: ignore[misc]
    for shared_list in (asset.top_keys, asset.first_record_keys, asset.notes):
        with pytest.raises(FrozenDataError):
            shared_list.append("injected")
    with pytest.raises(FrozenDataError):
        loaded.main_assets["SEA-C"] = asset  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        loaded.payloads = {}  # type: ignore[misc]
    # A whole asset cannot be replaced or removed either.
    with pytest.raises(FrozenDataError):
        loaded.payloads[SAMPLE] = {}
    with pytest.raises(FrozenDataError):
        loaded.payloads.pop(SAMPLE)
    with pytest.raises(FrozenDataError):
        loaded.payloads.clear()
    assert type(loaded.payloads) is FrozenDict and type(loaded.main_assets) is FrozenDict
    # The lists handed out by get_catalog / find_assets are the caller's own.
    catalog = data_catalog.get_catalog()
    catalog.clear()
    assert len(data_catalog.get_catalog()) == len(loaded.catalog) > 100


# --------------------------------------------------------------------------- #
# it still reads exactly like plain parsed JSON (what the connectors rely on)
# --------------------------------------------------------------------------- #
def test_frozen_payloads_equal_the_files_on_disk(fresh_data_store):
    loaded = data_catalog.init_data()

    for key, payload in loaded.payloads.items():
        with resolve_data_path(key, loaded.root).open(encoding="utf-8") as handle:
            plain = json.load(handle)
        assert payload == plain, key
        assert json.dumps(payload) == json.dumps(plain), key  # same order, same bytes


def test_frozen_containers_pass_the_checks_connectors_make():
    payload = data_catalog.load_json_asset(SAMPLE)
    record = _first(payload, dict)
    records = _first(payload, list)

    assert isinstance(payload, dict) and isinstance(record, dict)
    assert isinstance(records, list)
    assert record.get(next(iter(record))) == record[next(iter(record))]
    assert record.get("no-such-key", "fallback") == "fallback"
    assert list(record) == list(record.keys()) == [key for key, _ in record.items()]
    assert len(records) == len(list(records)) == sum(1 for _ in records)
    assert records[0] is next(iter(records))
    assert records[-1] is records[len(records) - 1]
    assert (record | {"extra": 1})["extra"] == 1
    assert json.loads(json.dumps(record)) == record


def test_copies_are_plain_and_belong_to_the_caller():
    """A connector that copies a record and changes its copy keeps working."""
    payload = data_catalog.load_json_asset(SAMPLE)
    record = _first(payload, dict)
    records = _first(payload, list)
    before = json.dumps(payload, sort_keys=True)

    dict_copies = [dict(record), record.copy(), {**record}, copy.copy(record), record | {}]
    for own in dict_copies:
        assert type(own) is dict
        own["injected"] = "changed"
        own.pop(next(iter(record)))
    list_copies = [
        list(records),
        records.copy(),
        records[:],
        records[1:],
        [*records],
        sorted(records, key=repr),
        copy.copy(records),
        records + [],
    ]
    for own_list in list_copies:
        assert type(own_list) is list
        own_list.append("injected")
        own_list.sort(key=repr)
        own_list.clear()

    assert json.dumps(payload, sort_keys=True) == before
    assert "injected" not in record


def test_deep_copy_is_plain_at_every_depth_and_independent():
    payload = data_catalog.load_json_asset(SAMPLE)
    before = json.dumps(payload, sort_keys=True)

    for own in (copy.deepcopy(payload), pickle.loads(pickle.dumps(payload))):
        assert own == payload
        assert all(type(node) in (dict, list) for node in _walk(own))
        for node in list(_walk(own)):
            if isinstance(node, dict):
                node["injected"] = "changed"
            else:
                node.append("injected")
        assert own != payload

    assert json.dumps(payload, sort_keys=True) == before


def test_a_shallow_copy_does_not_unfreeze_the_nested_shared_data():
    """dict(record) is the caller's, but what it points to is still shared."""
    payload = data_catalog.load_json_asset(SAMPLE)
    holder = next(
        node
        for node in _walk(payload)
        if isinstance(node, dict) and any(isinstance(v, (dict, list)) for v in node.values())
    )
    nested_key = next(key for key, value in holder.items() if isinstance(value, (dict, list)))

    own = dict(holder)

    with pytest.raises(FrozenDataError):
        _set_item(own[nested_key])


def test_freeze_helper():
    plain = {"a": [1, {"b": [2, 3]}], "c": None, "d": "text", "e": 1.5, "f": True}

    frozen = freeze(plain)

    assert frozen == plain
    assert freeze(frozen) == frozen  # freezing again changes nothing
    assert [type(node) for node in _walk(frozen)] == [FrozenDict, FrozenList, FrozenDict, FrozenList]
    assert all(type(node) in (dict, list) for node in _walk(plain))  # the input is untouched
    for scalar in ("text", 3, 1.5, True, None):
        assert freeze(scalar) is scalar


# --------------------------------------------------------------------------- #
# one run cannot change what another run reads
# --------------------------------------------------------------------------- #
def test_one_run_cannot_change_what_another_run_reads(fresh_data_store, sample_requests):
    loaded = data_catalog.init_data()
    request = sample_requests()[0]
    first_run = build_fact_package_for_request(request).model_dump_json()

    # A "run" that tries to rewrite every container of every shared asset.
    refused = 0
    for payload in loaded.payloads.values():
        for node in _walk(payload):
            try:
                _set_item(node)
            except FrozenDataError:
                refused += 1
            else:  # pragma: no cover - a container that accepted the change
                raise AssertionError(f"a shared {type(node).__name__} was modified")
    data_catalog.clear_connector_caches()  # the next run goes back to the store

    assert refused > 20_000
    assert build_fact_package_for_request(request).model_dump_json() == first_run


def test_layer2_output_is_identical_with_frozen_and_with_plain_data(
    fresh_data_store, sample_requests, monkeypatch
):
    """Freezing must not change a single fact: same bytes as with plain dict/list."""

    def run_everything() -> list[str]:
        out = [build_fact_package_for_request(r).model_dump_json() for r in sample_requests()]
        for block_id in sorted(BLOCK_REGISTRY):
            for request in sample_requests():
                out.append(BLOCK_REGISTRY[block_id](request).model_dump_json())
        return out

    with monkeypatch.context() as plain_data:
        plain_data.setattr(data_catalog, "freeze", lambda value: value)
        data_catalog.reset_data()
        loaded = data_catalog.init_data()
        assert all(type(n) in (dict, list) for p in loaded.payloads.values() for n in _walk(p))
        with_plain = run_everything()

    data_catalog.reset_data()
    loaded = data_catalog.init_data()
    assert all(
        type(n) in (FrozenDict, FrozenList) for p in loaded.payloads.values() for n in _walk(p)
    )
    with_frozen = run_everything()

    assert len(with_frozen) == 6 + 6 * len(BLOCK_REGISTRY)
    assert with_frozen == with_plain
    # Guard against "identical" meaning "both empty".
    assert sum('"status":"found"' in text for text in with_frozen) >= 20
