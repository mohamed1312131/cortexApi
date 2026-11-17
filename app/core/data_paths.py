"""Data-root resolution for the Layer 2 data assets (S2).

Connectors and the data catalogue name their files with *logical* paths such as
``data/air/cortex_air_block_c_dataset.json``. A logical path is an identifier:
it is what ends up in ``Provenance.source`` and it never depends on the machine.

This module turns a logical path into an absolute path under the data root, so
the process working directory no longer decides which files are read.

Data root = ``CORTEX_DATA_DIR`` if set (environment, then ``.env``), otherwise
``<repo>/data`` resolved from this package's location.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.config import settings

DATA_DIR_ENV = "CORTEX_DATA_DIR"
LOGICAL_PREFIX = "data"

# app/core/data_paths.py -> parents[2] is the repository root (``/app`` in Docker).
PACKAGE_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def data_root() -> Path:
    """Return the absolute data root. Never depends on the working directory
    unless ``CORTEX_DATA_DIR`` itself is given as a relative path."""
    configured = os.environ.get(DATA_DIR_ENV, "").strip() or settings.cortex_data_dir.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return PACKAGE_DATA_ROOT


def resolve_data_path(path: str | Path, root: Path | None = None) -> Path:
    """Map a logical ``data/<mode>/<file>`` path to an absolute path under the data root.

    Absolute paths are returned unchanged.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    parts = candidate.parts
    if parts and parts[0] == LOGICAL_PREFIX:
        candidate = Path(*parts[1:])
    return (root if root is not None else data_root()) / candidate


def logical_data_path(path: str | Path, root: Path | None = None) -> str:
    """Return the logical ``data/<mode>/<file>`` form of a path (POSIX separators).

    Relative paths are normalised to start with ``data/``. Absolute paths under the
    data root are mapped back to their logical form; other absolute paths are
    returned as they are.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        base = root if root is not None else data_root()
        try:
            relative = candidate.relative_to(base)
        except ValueError:
            return candidate.as_posix()
        return (Path(LOGICAL_PREFIX) / relative).as_posix()
    parts = candidate.parts
    if parts and parts[0] == LOGICAL_PREFIX:
        return candidate.as_posix()
    return (Path(LOGICAL_PREFIX) / candidate).as_posix()
