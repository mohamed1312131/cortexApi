from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.fact_package import FactPackage
from app.schemas.intake import IntakeResult
from app.schemas.layer2_summary import Layer2Summary
from app.schemas.layer3 import Layer3Result
from app.schemas.layer4 import Layer4Result


class CortexNextAction(str, Enum):
    ask_user = "ASK_USER"
    show_fact_package = "SHOW_FACT_PACKAGE"
    error = "ERROR"


class CortexOrchestratorDebug(BaseModel):
    layer2_ran: bool = False
    rerun_scope: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    error: str | None = None


class CortexOrchestratorResult(BaseModel):
    conversation_id: str | None = None
    case_id: str
    layer1: IntakeResult
    layer2: FactPackage | None = None
    next_action: CortexNextAction
    debug: CortexOrchestratorDebug = Field(default_factory=CortexOrchestratorDebug)


class CortexFullNextAction(str, Enum):
    ask_user = "ASK_USER"
    show_report = "SHOW_REPORT"
    error = "ERROR"


class CortexFullOrchestratorDebug(BaseModel):
    layer2_ran: bool = False
    layer3_ran: bool = False
    layer4_ran: bool = False
    route: str | None = None
    rerun_scope: dict[str, Any] = Field(default_factory=dict)
    cache: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    error: str | None = None


class CortexFullOrchestratorResult(BaseModel):
    conversation_id: str | None = None
    case_id: str
    assistant_message: str
    layer1: IntakeResult
    layer2_summary: Layer2Summary | None = None
    artifact_refs: dict[str, str] = Field(default_factory=dict)
    layer2: FactPackage | None = None
    layer3: Layer3Result | None = None
    layer4: Layer4Result | None = None
    next_action: CortexFullNextAction
    debug: CortexFullOrchestratorDebug = Field(default_factory=CortexFullOrchestratorDebug)
