from __future__ import annotations

from app.schemas import ValidatedShipmentRequest
from app.services.layer1.cargo_profile_detector import apply_cargo_profiles
from app.services.layer1.semantic_validator import (
    apply_cargo_un_conflict_validation,
    normalize_and_validate_request,
)
from app.services.layer1.state import IntakeGraphState


def validate_and_normalize(state: IntakeGraphState) -> IntakeGraphState:
    current = state.get("current_request")
    if state.get("multiple_shipments_detected"):
        return {
            **state,
            "validation_errors": [],
            "validation_warnings": [],
            "rejected_fields": _rejected_fields(current),
        }

    if current is None:
        return {
            **state,
            "validation_errors": [],
            "validation_warnings": [],
            "rejected_fields": _rejected_fields(current),
        }

    changed_fields = list(state.get("changed_fields", []))
    current, validation_changed_fields = normalize_and_validate_request(current)
    changed_fields.extend(validation_changed_fields)

    before_profiles = current.model_copy(deep=True)
    current = apply_cargo_profiles(current)
    changed_fields.extend(_profile_changed_fields(before_profiles, current))

    current, conflict_changed_fields = apply_cargo_un_conflict_validation(current)
    changed_fields.extend(conflict_changed_fields)

    current = _boost_confirmed_field_confidence(current)

    before_raw_lane = current.model_copy(deep=True)
    current = _normalize_raw_lane_from_city(current)
    changed_fields.extend(_raw_lane_changed_fields(before_raw_lane, current))

    return {
        **state,
        "current_request": current,
        "changed_fields": _dedupe(changed_fields),
        "validation_errors": [],
        "validation_warnings": _validation_warnings(current),
        "rejected_fields": _rejected_fields(current),
    }


def _validation_warnings(request: ValidatedShipmentRequest) -> list:
    warnings = request.inferred_flags.get("validation_warnings", [])
    return warnings if isinstance(warnings, list) else []


def _profile_changed_fields(
    before: ValidatedShipmentRequest,
    after: ValidatedShipmentRequest,
) -> list[str]:
    changed: list[str] = []
    if before.active_profiles != after.active_profiles:
        changed.append("active_profiles")
    for field_name in after.cargo_flags.__class__.model_fields:
        if getattr(before.cargo_flags, field_name) != getattr(after.cargo_flags, field_name):
            changed.append(f"cargo_flags.{field_name}")
    for profile_name, profile_payload in after.profiles.items():
        if before.profiles.get(profile_name) != profile_payload:
            changed.append(f"profiles.{profile_name}")
    return changed


def _boost_confirmed_field_confidence(
    request: ValidatedShipmentRequest,
) -> ValidatedShipmentRequest:
    dg_profile = request.profiles.get("dangerous_goods", {})
    un_number = dg_profile.get("un_number")

    if un_number:
        request.field_confidence["un_number"] = max(
            request.field_confidence.get("un_number", 0.0),
            0.95,
        )

    if request.lane.origin_city:
        request.field_confidence["origin_city"] = max(
            request.field_confidence.get("origin_city", 0.0),
            0.9,
        )

    if request.lane.destination_city:
        request.field_confidence["destination_city"] = max(
            request.field_confidence.get("destination_city", 0.0),
            0.9,
        )

    if request.core_shipment.volume_cbm is not None:
        request.field_confidence["volume_cbm"] = max(
            request.field_confidence.get("volume_cbm", 0.0),
            0.9,
        )

    return request


def _normalize_raw_lane_from_city(
    request: ValidatedShipmentRequest,
) -> ValidatedShipmentRequest:
    if request.lane.origin_city:
        request.lane.origin_raw = request.lane.origin_city

    if request.lane.destination_city:
        request.lane.destination_raw = request.lane.destination_city

    if request.lane.origin_raw:
        request.facts_from_user["origin_raw"] = request.lane.origin_raw

    if request.lane.destination_raw:
        request.facts_from_user["destination_raw"] = request.lane.destination_raw

    return request


def _raw_lane_changed_fields(
    before: ValidatedShipmentRequest,
    after: ValidatedShipmentRequest,
) -> list[str]:
    changed: list[str] = []

    if before.lane.origin_raw != after.lane.origin_raw:
        changed.append("lane.origin_raw")

    if before.lane.destination_raw != after.lane.destination_raw:
        changed.append("lane.destination_raw")

    return changed


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result

def _rejected_fields(request: ValidatedShipmentRequest) -> list:
    rejected = request.inferred_flags.get("rejected_fields", [])
    return rejected if isinstance(rejected, list) else []
