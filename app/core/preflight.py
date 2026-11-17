"""Startup preflight: the checks that must pass before the API serves.

* the API-key configuration is valid (``app.core.auth``);
* the Layer 2 data root exists and every required data asset loads
  (``app.services.layer2.data_catalog``).

The FastAPI lifespan runs ``run_preflight()`` in every worker process. The
container entrypoint also runs it once, before uvicorn
(``python -m app.core.preflight``): with several uvicorn workers a failing
lifespan does not stop the container, because the supervisor keeps restarting
the workers. Running the same checks first makes a bad configuration or a
missing data file stop the container with one clear error.
"""

from __future__ import annotations

import sys

from app.core.auth import ApiKeyConfigError, validate_api_key_config
from app.core.logging import configure_logging
from app.services.layer2.data_catalog import DataLoadError, LoadedData, init_data


def run_preflight() -> LoadedData:
    """Validate the configuration and load the data. Raises on the first problem.

    Raises ``ApiKeyConfigError`` or ``DataLoadError``; both name what is wrong.
    """
    validate_api_key_config()
    return init_data()


def main() -> int:
    configure_logging()
    try:
        loaded = run_preflight()
    except (ApiKeyConfigError, DataLoadError) as exc:
        print(f"preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        f"preflight ok: data root {loaded.root}, "
        f"{len(loaded.catalog)} catalogued files, {len(loaded.payloads)} loaded assets"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
