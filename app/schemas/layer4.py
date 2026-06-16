from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.fact_package import FactPackage
from app.schemas.layer3 import Layer3Result
from app.schemas.reasoning_decision import ReasoningDecision
from app.schemas.shipment_request import RequestedMode


class Layer4ReportType(str, Enum):
    full_report = "full_report"


class Layer4ReportRequest(BaseModel):
    report_type: Layer4ReportType = Layer4ReportType.full_report
    latest_user_message: str | None = None
    response_language: str = "auto"
    fact_package: FactPackage
    layer3_result: Layer3Result

    @property
    def reasoning_decision(self) -> ReasoningDecision | None:
        return self.layer3_result.reasoning_decision


class Layer4Result(BaseModel):
    case_id: str
    report_type: Layer4ReportType = Layer4ReportType.full_report
    assistant_message: str
    modes_reported: list[RequestedMode] = Field(default_factory=list)
    warnings_shown: list[str] = Field(default_factory=list)
    source_reasoning_decision_id: str | None = None
    debug: dict[str, Any] = Field(default_factory=dict)

