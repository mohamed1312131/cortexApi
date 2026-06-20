from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Layer2BlockSummary(BaseModel):
    block_id: str
    mode: str
    status: str
    hard_gate_count: int = 0
    unknown_count: int = 0
    missing_field_count: int = 0
    planning_factor_count: int = 0
    confidence_source: str | None = None
    confidence_cap: float | None = None
    data_keys: list[str] = Field(default_factory=list)


class Layer2CostSummary(BaseModel):
    block_id: str
    mode: str
    status: str
    cost_status: str | None = None
    estimate: dict[str, Any] | None = None
    basis: str | None = None
    currency: str | None = None


class Layer2Summary(BaseModel):
    case_id: str
    request_summary: dict[str, Any] = Field(default_factory=dict)
    completeness_status: str | None = None
    completeness_reasons: list[str] = Field(default_factory=list)
    modes_covered: list[str] = Field(default_factory=list)
    block_statuses: dict[str, str] = Field(default_factory=dict)
    blocks_called_count: int = 0
    blocks_failed: list[str] = Field(default_factory=list)
    blocks_empty: list[str] = Field(default_factory=list)
    hard_gates: list[dict[str, Any]] = Field(default_factory=list)
    hard_gates_total: int = 0
    unknowns: list[dict[str, Any]] = Field(default_factory=list)
    unknowns_total: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    missing_fields_total: int = 0
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    conflicts_total: int = 0
    confidence_cap_reasons: list[str] = Field(default_factory=list)
    confidence_cap_reasons_total: int = 0
    cost_summaries: list[Layer2CostSummary] = Field(default_factory=list)
    block_summaries: list[Layer2BlockSummary] = Field(default_factory=list)
    block_summaries_total: int = 0
    omitted: dict[str, int] = Field(default_factory=dict)
