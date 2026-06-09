from __future__ import annotations

import os

from app.schemas import ProviderUsed, Provenance

_ENV_PROVIDER = "LAYER2_PROVIDER"
_ENV_OVERRIDES = "LAYER2_PROVIDER_OVERRIDES"


def get_layer2_provider(block_id: str | None = None) -> ProviderUsed:
    overrides = _provider_overrides()
    if block_id is not None and block_id in overrides:
        return overrides[block_id]

    return _provider_from_value(os.getenv(_ENV_PROVIDER, "mock"))


def provenance_for(
    block_id: str,
    source: str,
    record_id: str | None = None,
) -> Provenance:
    return Provenance(
        source=source,
        record_id=record_id,
        provider_used=get_layer2_provider(block_id),
    )


def _provider_overrides() -> dict[str, ProviderUsed]:
    raw = os.getenv(_ENV_OVERRIDES, "")
    overrides: dict[str, ProviderUsed] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        block_id, provider = part.split("=", 1)
        normalized_block_id = block_id.strip()
        if not normalized_block_id:
            continue
        overrides[normalized_block_id] = _provider_from_value(provider)
    return overrides


def _provider_from_value(value: str | None) -> ProviderUsed:
    normalized = str(value or "").strip().lower()
    try:
        return ProviderUsed(normalized)
    except ValueError:
        return ProviderUsed.mock
