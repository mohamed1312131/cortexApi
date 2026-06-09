from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schemas import BlockResponse, FactPackage


def summarize_block_response(response: BlockResponse) -> dict[str, Any]:
    provenance = getattr(response, "provenance", None)
    confidence = getattr(response, "confidence", None)

    return {
        "block_id": response.block_id,
        "mode": response.mode,
        "status": response.status,
        "provider_used": provenance.provider_used if provenance else None,
        "source": provenance.source if provenance else None,
        "record_id": provenance.record_id if provenance else None,
        "hard_gate_count": len(response.hard_gates),
        "unknown_count": len(response.unknowns),
        "missing_field_count": len(response.missing_fields),
        "confidence_source": confidence.source_confidence if confidence else None,
        "confidence_cap": confidence.cap if confidence else None,
    }


def build_layer2_trace(fact_package: FactPackage) -> dict[str, Any]:
    return {
        "case_id": fact_package.case_id,
        "request_mode": fact_package.request.mode.requested_mode,
        "planned_blocks": [
            item.block_id for item in fact_package.fetch_plan.items
        ],
        "called_blocks": fact_package.derived_rollup.blocks_called,
        "failed_blocks": fact_package.derived_rollup.blocks_failed,
        "empty_blocks": fact_package.derived_rollup.blocks_empty,
        "modes_covered": fact_package.derived_rollup.modes_covered,
        "hard_gate_count": len(fact_package.derived_rollup.hard_gates),
        "unknown_count": len(fact_package.derived_rollup.unknowns),
        "missing_field_count": len(fact_package.derived_rollup.missing_fields),
        "confidence_cap_count": len(fact_package.derived_rollup.confidence_caps),
        "conflict_count": len(fact_package.conflicts),
        "conflict_types": [conflict.type for conflict in fact_package.conflicts],
        "completeness_status": fact_package.completeness.status,
        "block_summaries": [
            summarize_block_response(response)
            for response in fact_package.block_responses
        ],
    }


def write_layer2_trace_json(
    fact_package: FactPackage,
    path: str | Path,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_layer2_trace(fact_package), indent=2, default=str),
        encoding="utf-8",
    )
    return output_path
