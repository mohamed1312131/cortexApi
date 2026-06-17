from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.layer2.data_catalog import build_data_catalog, summarize_catalog  # noqa: E402


def main() -> int:
    catalog = build_data_catalog()
    print(json.dumps(summarize_catalog(), indent=2, sort_keys=True))
    for asset in catalog:
        count = asset.record_count if asset.record_count is not None else "-"
        list_key = asset.list_key or "-"
        print(
            " | ".join(
                [
                    asset.path,
                    asset.mode,
                    asset.block_id or "-",
                    asset.role,
                    asset.connector_status,
                    asset.top_type,
                    f"{count}/{list_key}",
                ]
            )
        )

    return 1 if any(asset.top_type == "invalid" for asset in catalog) else 0


if __name__ == "__main__":
    raise SystemExit(main())
