from __future__ import annotations

from app.schemas import FactPackage, ValidatedShipmentRequest
from app.services.layer2.fact_package_builder import build_fact_package
from app.services.layer2.fetch_executor import execute_fetch_plan
from app.services.layer2.fetch_planner import build_fetch_plan


def build_fact_package_for_request(
    request: ValidatedShipmentRequest,
) -> FactPackage:
    plan = build_fetch_plan(request)
    responses = execute_fetch_plan(request, plan)
    return build_fact_package(request, plan, responses)
