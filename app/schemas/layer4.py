from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.fact_package import FactPackage
from app.schemas.layer2_summary import Layer2Summary
from app.schemas.layer3 import Layer3Result
from app.schemas.operational_evidence import OperationalEvidence
from app.schemas.reasoning_decision import ReasoningDecision
from app.schemas.shipment_request import RequestedMode


class Layer4ReportType(str, Enum):
    full_report = "full_report"


class Layer4ReportRequest(BaseModel):
    report_type: Layer4ReportType = Layer4ReportType.full_report
    latest_user_message: str | None = None
    response_language: str = "auto"
    fact_package: FactPackage | None = None
    layer2_summary: Layer2Summary | None = None
    layer3_result: Layer3Result
    operational_evidence: OperationalEvidence | None = None

    @model_validator(mode="after")
    def _requires_layer2_source(self) -> "Layer4ReportRequest":
        if self.fact_package is None and self.layer2_summary is None:
            raise ValueError("Layer 4 requires either fact_package or layer2_summary.")
        return self

    @property
    def reasoning_decision(self) -> ReasoningDecision | None:
        return self.layer3_result.reasoning_decision

    @property
    def case_id(self) -> str:
        if self.layer2_summary is not None:
            return self.layer2_summary.case_id
        if self.fact_package is not None:
            return self.fact_package.case_id
        return self.layer3_result.case_id


class Layer4Result(BaseModel):
    case_id: str
    report_type: Layer4ReportType = Layer4ReportType.full_report
    assistant_message: str
    modes_reported: list[RequestedMode] = Field(default_factory=list)
    warnings_shown: list[str] = Field(default_factory=list)
    source_reasoning_decision_id: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)
