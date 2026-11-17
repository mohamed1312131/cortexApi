"""Layer 2 data catalogue and the process-wide loaded data store.

Two things live here:

* the catalogue builder (``build_data_catalog``): a pure scan of the data root
  that describes every JSON file (mode, block, role, shape);
* the loaded store (``init_data`` / ``load_json_asset`` / ``get_main_asset``):
  the catalogue and the data assets the connectors use, read from disk **once**
  per process and then shared read-only (S1). "Read-only" is enforced: every
  payload is frozen when it is loaded (``FrozenDict`` / ``FrozenList``), so one
  run can never change what another run reads.

The data root is absolute (``app.core.data_paths``), so the working directory
does not matter, and ``init_data`` refuses to load when a required asset is
missing or does not parse (S2).

Paths stored on ``DataAsset.path`` and used as store keys are *logical* paths
(``data/<mode>/<file>``): they identify an asset and are what connectors put in
``Provenance.source``. They are never absolute machine paths.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.data_paths import (
    DATA_DIR_ENV,
    LOGICAL_PREFIX,
    data_root,
    logical_data_path,
    resolve_data_path,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# File names (lower case) of the main dataset of each block.
MAIN_DATASET_FILENAMES = frozenset(
    {
        "road_c_corridor_viability.json",
        "road_b_vehicle_fit_profiles.json",
        "road_f_document_requirements.json",
        "sea_a_dg_sea_acceptance.json",
        "sea_b_container_fit_rules.json",
        "sea_c_port_capability.json",
        "sea_d_carrier_trade_lane_reference.json",
        "sea_f_maritime_documents_border_gates.json",
        "sea_i_chokepoints_schedule_readiness.json",
        "sea_cost_reference.json",
        "road_cost_reference.json",
        "cortex_air_block_a_dg_records_repaired.json",
        "cortex_air_block_b_dataset.json",
        "cortex_air_block_c_dataset.json",
        "cortex_air_block_d_dataset.json",
        "cortex_air_block_e_dataset.json",
        "cortex_air_block_f_dataset.json",
        "cortex_air_block_h_dataset.json",
        "cortex_air_block_i_dataset.json",
    }
)


class DataLoadError(RuntimeError):
    """The data root or a required data asset is missing or does not parse."""


# --------------------------------------------------------------------------- #
# Frozen containers: the shared data cannot be modified
# --------------------------------------------------------------------------- #
class FrozenDataError(TypeError):
    """An attempt to modify the shared, read-only Layer 2 data."""


def _refuse_mutation(self: Any, *_args: Any, **_kwargs: Any) -> Any:
    raise FrozenDataError(
        f"Layer 2 data is shared between runs and read-only: this {type(self).__name__} "
        "cannot be modified. Work on your own copy (dict(x), list(x), copy.deepcopy(x))."
    )


class FrozenDict(dict):
    """A ``dict`` that refuses every modification.

    It stays a real ``dict`` on purpose: ``isinstance(x, dict)``, ``json.dumps``,
    ``dict(x)``, ``x.get`` and iteration behave exactly as for a plain dict, so
    connectors read it unchanged. Reading costs nothing extra (no read method is
    overridden). ``dict(x)`` / ``x.copy()`` / ``copy.copy(x)`` give a plain,
    modifiable shallow copy (its nested values are still frozen);
    ``copy.deepcopy(x)`` gives a plain, fully modifiable copy.
    """

    __slots__ = ()

    __setitem__ = _refuse_mutation
    __delitem__ = _refuse_mutation
    __ior__ = _refuse_mutation
    clear = _refuse_mutation
    pop = _refuse_mutation
    popitem = _refuse_mutation
    setdefault = _refuse_mutation
    update = _refuse_mutation

    def __reduce__(self) -> tuple[Any, ...]:
        # copy.copy, copy.deepcopy and pickle all rebuild an object from this:
        # they give back a plain dict (deep-copied by deepcopy and pickle).
        return (dict, (dict(self),))


class FrozenList(list):
    """A ``list`` that refuses every modification. See ``FrozenDict``.

    Slices, ``list(x)``, ``sorted(x)``, ``x + other`` and ``x.copy()`` give
    plain, modifiable lists.
    """

    __slots__ = ()

    __setitem__ = _refuse_mutation
    __delitem__ = _refuse_mutation
    __iadd__ = _refuse_mutation
    __imul__ = _refuse_mutation
    append = _refuse_mutation
    clear = _refuse_mutation
    extend = _refuse_mutation
    insert = _refuse_mutation
    pop = _refuse_mutation
    remove = _refuse_mutation
    reverse = _refuse_mutation
    sort = _refuse_mutation

    def __reduce__(self) -> tuple[Any, ...]:
        # As for FrozenDict: copies and pickles are plain lists.
        return (list, (list(self),))


# JSON leaves: immutable already. Checked inline in ``freeze`` so that the
# leaves (the large majority of the values) do not cost a function call each.
_JSON_LEAF_TYPES = frozenset({str, int, float, bool, type(None)})


def freeze(value: Any) -> Any:
    """Return a deeply frozen copy of parsed JSON (dicts and lists, any depth).

    Scalars (str, int, float, bool, None) are immutable already and are returned
    as they are. Called once per asset at load time, never per request.
    """
    if isinstance(value, dict):
        return FrozenDict(
            {
                key: item if type(item) in _JSON_LEAF_TYPES else freeze(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        return FrozenList(
            [item if type(item) in _JSON_LEAF_TYPES else freeze(item) for item in value]
        )
    return value


@dataclass(frozen=True)
class DataAsset:
    path: str
    mode: str
    block_id: str | None
    role: str
    purpose: str
    top_type: str
    top_keys: list[str]
    list_key: str | None
    record_count: int | None
    first_record_keys: list[str]
    connector_status: str
    notes: list[str] = field(default_factory=list)


def infer_mode_from_path(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "road" in parts:
        return "road"
    if "sea" in parts:
        return "sea"
    if "air" in parts:
        return "air"
    return "unknown"


def infer_block_id_from_filename(path: Path) -> str | None:
    name = path.name.lower()
    exact = {
        "air_airports.json": "AIR-C",
        "air_carriers.json": "AIR-D",
        "air_reference.json": "AIR-COST",
    }
    if name in exact:
        return exact[name]

    prefix_map = [
        ("road_a_", "ROAD-A"),
        ("road_b_", "ROAD-B"),
        ("road_c_", "ROAD-C"),
        ("road_f_", "ROAD-F"),
        ("road_cost_", "ROAD-COST"),
        ("sea_a_", "SEA-A"),
        ("sea_b_", "SEA-B"),
        ("sea_c_", "SEA-C"),
        ("sea_d_", "SEA-D"),
        ("sea_f_", "SEA-F"),
        ("sea_i_", "SEA-I"),
        ("sea_cost_", "SEA-COST"),
        ("cortex_air_block_a_", "AIR-A"),
        ("cortex_air_block_b_", "AIR-B"),
        ("cortex_air_block_c_", "AIR-C"),
        ("cortex_air_block_d_", "AIR-D"),
        ("cortex_air_block_e_", "AIR-E"),
        ("cortex_air_block_f_", "AIR-F"),
        ("cortex_air_block_h_", "AIR-H"),
        ("cortex_air_block_i_", "AIR-I"),
    ]
    for prefix, block_id in prefix_map:
        if name.startswith(prefix):
            return block_id
    return None


def infer_role_from_filename(path: Path) -> str:
    name = path.name.lower()
    if name in MAIN_DATASET_FILENAMES:
        return "main"

    if name in {"air_reference.json", "air_airports.json", "air_carriers.json"}:
        return "support"
    if "metadata" in name:
        return "metadata"
    if "confidence" in name:
        return "confidence"
    if "coverage" in name:
        return "coverage"
    if "source_refs" in name or "source_inventory" in name:
        return "source_refs"

    rules_keywords = [
        "readiness_rules",
        "validation_rules",
        "fit_rules",
        "border_rules",
        "route_feasibility_rules",
        "route_risk_rules",
        "security_status_matrix",
        "jurisdiction_security_rules",
        "category_rules",
        "pair_generation_rules",
    ]
    if any(keyword in name for keyword in rules_keywords):
        return "rules"

    support_keywords = [
        "field_mapping",
        "schema",
        "stowage_category_mapping",
        "country_groups",
        "jurisdiction_map",
    ]
    if any(keyword in name for keyword in support_keywords):
        return "support"

    return "unknown"


def connector_status_for(block_id: str | None, path: Path) -> str:
    if block_id is None:
        return "not_planned"

    status_by_block = {
        "ROAD-C": "implemented",
        "ROAD-A": "partial",
        "ROAD-B": "implemented",
        "ROAD-F": "implemented",
        "ROAD-COST": "implemented",
        "SEA-C": "implemented",
        "SEA-B": "implemented",
        "SEA-F": "partial",
        "SEA-A": "implemented",
        "SEA-D": "implemented",
        "SEA-I": "implemented",
        "SEA-COST": "implemented",
        "AIR-A": "implemented",
        "AIR-B": "implemented",
        "AIR-C": "implemented",
        "AIR-D": "implemented",
        "AIR-E": "implemented",
        "AIR-F": "implemented",
        "AIR-H": "implemented",
        "AIR-I": "implemented",
        "AIR-COST": "implemented",
    }
    return status_by_block.get(block_id, "planned")


_Inspection = tuple[str, list[str], str | None, int | None, list[str], list[str]]


def _read_json_file(path: Path) -> Any:
    """Read and parse one JSON file. Raises ``OSError`` / ``json.JSONDecodeError``."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def inspect_json_file(path: Path) -> _Inspection:
    try:
        payload = _read_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        return "invalid", [], None, None, [], [f"json inspection failed: {exc}"]
    return _inspect_payload(payload)


def _inspect_payload(payload: Any) -> _Inspection:
    notes: list[str] = []
    if isinstance(payload, dict):
        top_keys = list(payload.keys())[:30]
        list_key = None
        record_count = None
        first_record_keys: list[str] = []
        for key, value in payload.items():
            if isinstance(value, list):
                list_key = key
                record_count = len(value)
                if value and isinstance(value[0], dict):
                    first_record_keys = list(value[0].keys())[:80]
                break
        return "dict", top_keys, list_key, record_count, first_record_keys, notes

    if isinstance(payload, list):
        first_record_keys = []
        if payload and isinstance(payload[0], dict):
            first_record_keys = list(payload[0].keys())[:80]
        return "list", [], None, len(payload), first_record_keys, notes

    return "scalar", [], None, None, [], notes


def _iter_json_files(root: Path) -> list[tuple[Path, Path]]:
    """Return ``(absolute path, logical path)`` for every JSON file under the root."""
    files: list[tuple[Path, Path]] = []
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        files.append((path, Path(LOGICAL_PREFIX) / relative))
    return files


def _describe_asset(logical: Path, inspection: _Inspection) -> DataAsset:
    mode = infer_mode_from_path(logical)
    block_id = infer_block_id_from_filename(logical)
    role = infer_role_from_filename(logical)
    (
        top_type,
        top_keys,
        list_key,
        record_count,
        first_record_keys,
        notes,
    ) = inspection
    status = connector_status_for(block_id, logical)
    # A DataAsset is shared too (get_main_asset / find_assets hand the same
    # object to every caller): its list fields are frozen like the payloads.
    return DataAsset(
        path=logical.as_posix(),
        mode=mode,
        block_id=block_id,
        role=role,
        purpose=_purpose_for(logical, block_id, role),
        top_type=top_type,
        top_keys=FrozenList(top_keys),
        list_key=list_key,
        record_count=record_count,
        first_record_keys=FrozenList(first_record_keys),
        connector_status=status,
        notes=FrozenList(notes + _notes_for(block_id, role, top_type)),
    )


def build_data_catalog(root: Path | None = None) -> list[DataAsset]:
    """Scan the data root from disk and describe every JSON file.

    This reads every file. Request-time code must not call it: use
    ``get_catalog`` / ``find_assets`` / ``get_main_asset``, which read the
    catalogue loaded once by ``init_data``.
    """
    base = Path(root).resolve() if root is not None else data_root()
    return [
        _describe_asset(logical, inspect_json_file(path))
        for path, logical in _iter_json_files(base)
    ]


# --------------------------------------------------------------------------- #
# Loaded store: catalogue + used assets, read once per process (S1, S2)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LoadedData:
    """Everything Layer 2 needs from disk, loaded once and shared read-only."""

    root: Path
    catalog: tuple[DataAsset, ...]
    # block id -> the asset of its main dataset. A FrozenDict.
    main_assets: dict[str, DataAsset]
    # logical path -> parsed JSON, deeply frozen (FrozenDict / FrozenList): a
    # payload cannot be modified. The mapping is a FrozenDict too, so an asset
    # cannot be replaced or removed; only ``load_json_asset`` adds to it (a
    # late-loaded file), under the lock, through ``_add_payload``.
    payloads: dict[str, Any]
    # logical paths that were required at load time (connector-declared + main).
    required_paths: frozenset[str]


_LOADED: LoadedData | None = None
_LOAD_ERROR: str | None = None
_LOAD_LOCK = threading.RLock()


def _connector_modules() -> list[Any]:
    from app.services.layer2 import connectors

    return [
        importlib.import_module(f"{connectors.__name__}.{info.name}")
        for info in pkgutil.iter_modules(connectors.__path__)
    ]


def connector_declared_paths() -> dict[str, list[str]]:
    """Logical path -> where it is declared, for every data file a connector names.

    Connectors declare their files as module-level ``Path`` constants
    (``DEFAULT_DATA_PATH``, ``_DATA_PATH``, ``*_RULES_PATH`` ...). Those constants
    are the list of "used" assets that ``init_data`` loads and requires.
    """
    declared: dict[str, list[str]] = {}
    for module in _connector_modules():
        short_name = module.__name__.rsplit(".", 1)[-1]
        for name, value in sorted(vars(module).items()):
            if isinstance(value, Path):
                declared.setdefault(logical_data_path(value), []).append(
                    f"{short_name}.{name}"
                )
    return declared


def _registry_block_ids() -> set[str]:
    from app.services.layer2.registry import BLOCK_REGISTRY

    return set(BLOCK_REGISTRY)


def _load(root: Path) -> LoadedData:
    if not root.is_dir():
        raise DataLoadError(
            f"Cortex data root does not exist or is not a directory: {root} "
            f"(set {DATA_DIR_ENV} to the folder that contains air/, sea/ and road/)."
        )

    declared = connector_declared_paths()
    block_ids = _registry_block_ids()

    catalog: list[DataAsset] = []
    payloads: dict[str, Any] = {}
    parse_errors: dict[str, str] = {}
    for path, logical in _iter_json_files(root):
        key = logical.as_posix()
        try:
            payload = _read_json_file(path)
        except (OSError, json.JSONDecodeError) as exc:
            parse_errors[key] = str(exc)
            inspection: _Inspection = (
                "invalid", [], None, None, [], [f"json inspection failed: {exc}"],
            )
        else:
            inspection = _inspect_payload(payload)
            payloads[key] = payload
        catalog.append(_describe_asset(logical, inspection))

    main_assets: dict[str, DataAsset] = {}
    for asset in catalog:
        if asset.role == "main" and asset.block_id is not None:
            main_assets.setdefault(asset.block_id, asset)

    # Required = every file a connector declares + the main dataset of every
    # block in the registry.
    required: dict[str, str] = {
        key: "declared by " + ", ".join(where) for key, where in declared.items()
    }
    present_main_names = {
        Path(asset.path).name.lower(): asset for asset in catalog if asset.role == "main"
    }
    for filename in sorted(MAIN_DATASET_FILENAMES):
        block_id = infer_block_id_from_filename(Path(filename))
        if block_id not in block_ids:
            continue
        asset = present_main_names.get(filename)
        if asset is not None:
            required.setdefault(asset.path, f"main dataset of {block_id}")
            continue
        mode = block_id.split("-", 1)[0].lower()
        expected = f"{LOGICAL_PREFIX}/{mode}/{filename}"
        # File names are matched case-insensitively here; do not report the same
        # missing file twice when a connector already declares it.
        if expected.lower() not in {key.lower() for key in required}:
            required[expected] = f"main dataset of {block_id}"

    problems: list[str] = []
    for key, why in sorted(required.items()):
        absolute = resolve_data_path(key, root)
        if key in parse_errors:
            problems.append(f"{absolute} is not valid JSON ({parse_errors[key]}) [{why}]")
        elif key not in payloads:
            problems.append(f"{absolute} is missing [{why}]")
    if problems:
        raise DataLoadError(
            f"Cortex data failed to load from {root}: "
            f"{len(problems)} required data asset(s) unusable: " + "; ".join(problems)
        )

    # Keep only what is used at request time; the rest of the 37 MB is dropped.
    # What is kept is frozen here, once: every run reads the same objects and
    # none of them can modify them.
    used_payloads = {key: freeze(payloads[key]) for key in required}
    return LoadedData(
        root=root,
        catalog=tuple(catalog),
        main_assets=FrozenDict(main_assets),
        payloads=FrozenDict(used_payloads),
        required_paths=frozenset(required),
    )


def clear_connector_caches() -> None:
    """Drop derived-record caches inside connectors (they hold loaded payloads)."""
    for module in _connector_modules():
        for value in vars(module).values():
            if getattr(value, "__module__", None) != module.__name__:
                continue  # imported helper, not this connector's own cache
            cache_clear = getattr(value, "cache_clear", None)
            if callable(cache_clear):
                cache_clear()


def init_data(root: Path | str | None = None, *, force: bool = False) -> LoadedData:
    """Load the catalogue and the used data assets once for this process.

    Call it at process start (the API does it in the FastAPI lifespan; workers
    and scripts call it explicitly). It is idempotent: a second call returns the
    already-loaded data unless ``force`` is set or a different root is given.

    Raises ``DataLoadError`` naming every required asset that is missing or does
    not parse. Nothing is loaded in that case.
    """
    global _LOADED, _LOAD_ERROR
    target = Path(root).expanduser().resolve() if root is not None else data_root()
    with _LOAD_LOCK:
        if _LOADED is not None and not force and _LOADED.root == target:
            return _LOADED
        had_previous = _LOADED is not None
        try:
            loaded = _load(target)
        except DataLoadError as exc:
            _LOADED = None
            _LOAD_ERROR = str(exc)
            if had_previous:
                clear_connector_caches()
            logger.error("layer2.data_load_failed root=%s error=%s", target, exc)
            raise
        _LOADED = loaded
        _LOAD_ERROR = None
        if had_previous:
            clear_connector_caches()
        logger.info(
            "layer2.data_loaded root=%s catalog_files=%d loaded_assets=%d",
            loaded.root,
            len(loaded.catalog),
            len(loaded.payloads),
        )
        return loaded


def reset_data() -> None:
    """Forget the loaded data (tests). The next access loads it again."""
    global _LOADED, _LOAD_ERROR
    with _LOAD_LOCK:
        _LOADED = None
        _LOAD_ERROR = None
        clear_connector_caches()


def is_data_loaded() -> bool:
    return _LOADED is not None


def data_load_error() -> str | None:
    """The error of the last failed load, if the data is currently not loaded."""
    return _LOAD_ERROR


def get_loaded_data() -> LoadedData:
    """Return the loaded data, loading it on first use if ``init_data`` was not called.

    The lazy path exists for tests and one-off scripts. It fails as loudly as
    ``init_data`` does: a wrong data folder raises ``DataLoadError`` instead of
    quietly turning every block into "unknown".
    """
    loaded = _LOADED
    if loaded is not None:
        return loaded
    return init_data()


def get_catalog() -> list[DataAsset]:
    return list(get_loaded_data().catalog)


def _add_payload(loaded: LoadedData, key: str, payload: Any) -> None:
    """Add a late-loaded asset to the frozen index. The one place that writes to
    it: callers hold ``_LOAD_LOCK`` and never replace an existing key."""
    if key in loaded.payloads:
        raise FrozenDataError(f"Layer 2 asset {key} is already loaded and cannot be replaced.")
    dict.__setitem__(loaded.payloads, key, payload)


def load_json_asset(path: str | Path) -> Any:
    """Return the parsed JSON of a data asset from the loaded store.

    Connectors call this instead of opening files. Assets declared by connectors
    are loaded by ``init_data`` and served from memory. A path that was not
    declared is read from disk the first time only, then served from memory;
    a missing or invalid file raises ``OSError`` / ``json.JSONDecodeError`` so the
    caller reports the block as unknown (never as clear).

    The returned object is shared by every run and frozen: modifying it (at
    any depth) raises ``FrozenDataError``. Copy what you need to change.
    """
    loaded = get_loaded_data()
    key = logical_data_path(path, loaded.root)
    try:
        return loaded.payloads[key]
    except KeyError:
        pass
    with _LOAD_LOCK:
        if key in loaded.payloads:
            return loaded.payloads[key]
        payload = freeze(_read_json_file(resolve_data_path(path, loaded.root)))
        _add_payload(loaded, key, payload)
        logger.warning(
            "layer2.data_asset_loaded_late path=%s (not declared as a connector "
            "path constant, so it was not loaded at startup)",
            key,
        )
        return payload


def find_assets(
    mode: str | None = None,
    block_id: str | None = None,
    role: str | None = None,
    connector_status: str | None = None,
) -> list[DataAsset]:
    assets = get_catalog()
    if mode is not None:
        assets = [asset for asset in assets if asset.mode == mode]
    if block_id is not None:
        assets = [asset for asset in assets if asset.block_id == block_id]
    if role is not None:
        assets = [asset for asset in assets if asset.role == role]
    if connector_status is not None:
        assets = [
            asset
            for asset in assets
            if asset.connector_status == connector_status
        ]
    return assets


def get_main_asset(block_id: str) -> DataAsset | None:
    return get_loaded_data().main_assets.get(block_id)


def _purpose_for(path: Path, block_id: str | None, role: str) -> str:
    if block_id is None:
        return f"Unmapped {role} data asset"
    return f"{role} data asset for {block_id}"


def _notes_for(block_id: str | None, role: str, top_type: str) -> list[str]:
    notes: list[str] = []
    if block_id is None:
        notes.append("no block_id inferred from filename")
    if role == "unknown":
        notes.append("role not inferred from filename")
    if top_type == "invalid":
        notes.append("invalid JSON; asset cannot be inspected")
    return notes
