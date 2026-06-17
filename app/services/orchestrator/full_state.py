from __future__ import annotations

from typing import TypedDict

from app.schemas.cortex_orchestrator import CortexFullOrchestratorResult
from app.schemas.fact_package import FactPackage
from app.schemas.intake import IntakeResult
from app.schemas.layer3 import Layer3Result
from app.schemas.layer4 import Layer4Result


class CortexFullState(TypedDict, total=False):
    message: str
    conversation_id: str | None
    case_id: str | None
    user_id: str | None
    company_id: str | None
    trace_id: str
    shipment_request_version: int | None
    cache_status: dict

    layer1: IntakeResult
    layer2: FactPackage
    layer3: Layer3Result
    layer4: Layer4Result

    result: CortexFullOrchestratorResult
    error: str
