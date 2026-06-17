from __future__ import annotations

from collections import Counter
from typing import Any

from app.schemas import (
    BlockResponse,
    BlockStatus,
    Conflict,
    RequestedMode,
)

_OBVIOUS_FIELD_KEYS = {
    "lane.origin_country": "origin_country",
    "lane.destination_country": "destination_country",
    "core_shipment.weight_kg": "weight_kg",
    "core_shipment.volume_cbm": "volume_cbm",
    "core_shipment.dimensions": "dimensions",
    "commercial.incoterm": "incoterm",
    "commercial.ready_date": "ready_date",
    "commercial.deadline": "deadline",
}


def detect_conflicts(block_responses: list[BlockResponse]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    conflicts.extend(_duplicate_block_response_conflicts(block_responses))
    conflicts.extend(_dg_unclear_conflicts(block_responses))
    conflicts.extend(_field_unknown_but_data_present_conflicts(block_responses))
    return conflicts


def _duplicate_block_response_conflicts(
    block_responses: list[BlockResponse],
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    counts = Counter((response.mode, response.block_id) for response in block_responses)

    for (mode, block_id), count in counts.items():
        if count <= 1:
            continue
        conflicts.append(
            Conflict(
                type="duplicate_block_response",
                message=f"Duplicate block response detected for {_mode_label(mode)}/{block_id}.",
                action="Executor should call each planned block once per mode.",
            )
        )

    return conflicts


def _dg_unclear_conflicts(
    block_responses: list[BlockResponse],
) -> list[Conflict]:
    conflicts: list[Conflict] = []

    for response in block_responses:
        if not response.block_id.endswith("-A"):
            continue
        if response.status not in {BlockStatus.skipped, BlockStatus.not_applicable}:
            continue
        if not _mode_has_dg_unknown(response.mode, response.block_id, block_responses):
            continue
        conflicts.append(
            Conflict(
                type="dg_unclear_but_dg_block_not_resolved",
                message=(
                    "Dangerous goods status appears unclear, but the mode DG "
                    "block did not produce a resolved check."
                ),
                action=(
                    "Planner should include DG block when dangerous goods is "
                    "unknown/likely/yes."
                ),
            )
        )

    return conflicts


def _mode_has_dg_unknown(
    mode: RequestedMode,
    dg_block_id: str,
    block_responses: list[BlockResponse],
) -> bool:
    for response in block_responses:
        if response.mode != mode or response.block_id == dg_block_id:
            continue
        for unknown in response.unknowns:
            text = " ".join(
                [
                    unknown.field,
                    unknown.reason,
                    unknown.impact or "",
                ]
            ).lower()
            if "dangerous_goods" in text or "dangerous goods" in text or " dg" in text:
                return True
    return False


def _field_unknown_but_data_present_conflicts(
    block_responses: list[BlockResponse],
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    emitted_fields: set[str] = set()

    for response in block_responses:
        for unknown in response.unknowns:
            if unknown.field not in _OBVIOUS_FIELD_KEYS:
                continue
            if unknown.field in emitted_fields:
                continue
            data_key = _OBVIOUS_FIELD_KEYS[unknown.field]
            if _another_response_has_value(
                block_responses,
                response,
                data_key,
            ):
                conflicts.append(
                    Conflict(
                        type="field_unknown_but_data_present",
                        message=(
                            f"Field {unknown.field} is marked unknown by one "
                            "block but appears present in another block response."
                        ),
                        action=(
                            "Builder should verify request-level field resolution "
                            "and block assumptions."
                        ),
                    )
                )
                emitted_fields.add(unknown.field)

    return conflicts


def _another_response_has_value(
    block_responses: list[BlockResponse],
    unknown_response: BlockResponse,
    data_key: str,
) -> bool:
    for response in block_responses:
        if response is unknown_response or response.status == BlockStatus.skipped:
            continue
        if _has_non_empty_value(response.data.get(data_key)):
            return True
    return False


def _has_non_empty_value(value: Any) -> bool:
    if value is None:
        return False
    if value == "":
        return False
    if value == []:
        return False
    if value == {}:
        return False
    return True


def _mode_label(mode: RequestedMode) -> str:
    return mode.value
