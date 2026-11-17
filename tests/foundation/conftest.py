"""Fixtures for the Wave 0 foundation tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from app.core.data_paths import DATA_DIR_ENV, PACKAGE_DATA_ROOT
from app.schemas import FlagState, RequestedMode, ValidatedShipmentRequest
from app.services.layer2 import data_catalog
from tests.prompt_budget_helpers import multimode_request

REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config: pytest.Config) -> None:
    # Registered here (pyproject.toml is not owned by the W0 follow-up task).
    config.addinivalue_line(
        "markers",
        "real_auth: keep the real API-key dependency for this test. Without the "
        "mark, tests/conftest.py::pinned_settings replaces it by one that answers "
        "with the pinned test tenant.",
    )


@pytest.fixture
def fresh_data_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start with nothing loaded and leave nothing loaded behind.

    The next test that needs data loads it again from the default root, so a
    test that breaks the data root on purpose cannot leak into other tests.
    """
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    data_catalog.reset_data()
    yield
    data_catalog.reset_data()


@pytest.fixture
def data_copy(tmp_path: Path) -> Path:
    """A separate data root that mirrors the real one with symlinks.

    Tests remove or replace files in it; the real data folder is never touched.
    """
    root = tmp_path / "relocated-data"
    for source in sorted(PACKAGE_DATA_ROOT.rglob("*.json")):
        relative = source.relative_to(PACKAGE_DATA_ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source)
    return root


def _dangerous_goods_request(un_number: str) -> ValidatedShipmentRequest:
    base = multimode_request()
    return base.model_copy(
        update={
            "case_id": f"case-foundation-dg-{un_number}",
            "cargo_flags": base.cargo_flags.model_copy(
                update={"dangerous_goods": FlagState.yes}
            ),
            "active_profiles": ["dangerous_goods"],
            "profiles": {"dangerous_goods": {"un_number": un_number}},
        }
    )


def _single_mode_request(mode: RequestedMode) -> ValidatedShipmentRequest:
    base = multimode_request()
    return base.model_copy(
        update={
            "case_id": f"case-foundation-{mode.value}",
            "mode": base.mode.model_copy(
                update={
                    "requested_mode": mode,
                    "candidate_modes": [mode],
                    "needs_mode_selection": False,
                }
            ),
        }
    )


@pytest.fixture
def sample_requests() -> Callable[[], list[ValidatedShipmentRequest]]:
    """Requests that together exercise every connector in the registry."""

    def _build() -> list[ValidatedShipmentRequest]:
        return [
            multimode_request(),
            _dangerous_goods_request("UN3480"),
            _dangerous_goods_request("UN1203"),
            _single_mode_request(RequestedMode.air),
            _single_mode_request(RequestedMode.sea),
            _single_mode_request(RequestedMode.road),
        ]

    return _build
