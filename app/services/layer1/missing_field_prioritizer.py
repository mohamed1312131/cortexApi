from __future__ import annotations

from app.schemas import MissingFields, ValidatedShipmentRequest
from app.services.layer1.semantic_validator import CARGO_UN_CONFLICT_BLOCKER


def prioritize_missing_fields(request: ValidatedShipmentRequest) -> MissingFields:
    blocking: list[str] = []
    high_value: list[str] = []
    can_wait: list[str] = []

    if not request.core_shipment.cargo_description:
        blocking.append("cargo description")

    if request.core_shipment.weight_kg is None and request.core_shipment.quantity is None:
        if _has_rejected_field(request, "core_shipment.weight_kg"):
            blocking.append("valid positive weight or quantity")
        else:
            blocking.append("weight or quantity")

    if not _has_origin(request) or not _has_destination(request):
        blocking.append("origin and destination")

    if _has_cargo_un_conflict(request):
        blocking.append(CARGO_UN_CONFLICT_BLOCKER)

    if _has_profile(request, "dangerous_goods"):
        if not _dangerous_goods_has_identifier(request):
            blocking.append("valid UN number or dangerous-goods classification")
        if not _has_profile(request, "lithium_battery"):
            _append_missing_profile_field(
                request,
                high_value,
                "dangerous_goods",
                "sds_available",
                "SDS availability",
            )

    if _has_profile(request, "lithium_battery"):
        if not request.lane.origin_city:
            blocking.append("origin city")
        if not request.lane.destination_city:
            blocking.append("destination city")
        _append_missing_profile_field(
            request,
            high_value,
            "lithium_battery",
            "packed_with_equipment",
            "battery packing configuration",
        )
        _append_missing_profile_field(
            request,
            high_value,
            "lithium_battery",
            "state_of_charge_pct",
            "state of charge for air",
        )
        _append_missing_profile_field(
            request,
            high_value,
            "lithium_battery",
            "un38_3_available",
            "UN38.3 availability",
        )

    if _has_profile(request, "oversized"):
        if not request.core_shipment.dimensions:
            blocking.append("dimensions")
        high_value.append("single-piece weight")
        high_value.append("stackability")
        high_value.append("lifting points")

    if _has_profile(request, "temperature_controlled"):
        _append_missing_profile_field(
            request,
            high_value,
            "temperature_controlled",
            "temperature_range",
            "temperature range",
        )
        _append_missing_profile_field(
            request,
            high_value,
            "temperature_controlled",
            "packaging_type",
            "cold-chain packaging type",
        )
        high_value.append("shelf life")

    if _has_profile(request, "pharma"):
        _append_missing_profile_field(
            request,
            high_value,
            "pharma",
            "cold_chain_required",
            "cold-chain requirement",
        )

    if _has_profile(request, "high_value") and request.commercial.cargo_value is None:
        high_value.append("cargo value")

    if _has_profile(request, "live_animals"):
        _append_missing_profile_field(
            request,
            blocking,
            "live_animals",
            "species",
            "animal species",
        )
        _append_missing_profile_field(
            request,
            high_value,
            "live_animals",
            "health_documents_available",
            "health documents availability",
        )

    if _has_profile(request, "vehicle"):
        _append_missing_profile_field(
            request,
            high_value,
            "vehicle",
            "fuel_status",
            "vehicle fuel/battery status",
        )

    if request.core_shipment.volume_cbm is None and request.core_shipment.dimensions is None:
        high_value.append("volume or dimensions")

    if request.commercial.ready_date is None and request.commercial.deadline is None:
        high_value.append("ready date or deadline")

    if request.commercial.incoterm is None:
        can_wait.append("incoterm")

    if request.commercial.cargo_value is None:
        can_wait.append("cargo value")

    if request.user_goal.priority.value == "unknown":
        can_wait.append("preferred budget/speed priority")

    return MissingFields(
        blocking=_dedupe(blocking),
        high_value=_dedupe(_without_existing(high_value, blocking)),
        can_wait=_dedupe(_without_existing(can_wait, blocking + high_value)),
    )


def _has_origin(request: ValidatedShipmentRequest) -> bool:
    return bool(request.lane.origin_country or request.lane.origin_city or request.lane.origin_raw)


def _has_destination(request: ValidatedShipmentRequest) -> bool:
    return bool(request.lane.destination_country or request.lane.destination_city or request.lane.destination_raw)


def _has_profile(request: ValidatedShipmentRequest, profile: str) -> bool:
    return profile in request.active_profiles


def _dangerous_goods_has_identifier(request: ValidatedShipmentRequest) -> bool:
    profile = request.profiles.get("dangerous_goods")
    if isinstance(profile, dict) and profile.get("un_number"):
        return True
    profile = request.profiles.get("lithium_battery")
    if isinstance(profile, dict):
        battery_type = profile.get("battery_type")
        if battery_type in {
            "lithium_ion_battery",
            "lithium_ion_with_equipment",
            "lithium_metal_battery",
            "lithium_metal_with_equipment",
        }:
            return True
    return bool(request.facts_from_user.get("un_number"))


def _has_cargo_un_conflict(request: ValidatedShipmentRequest) -> bool:
    conflicts = request.inferred_flags.get("validation_conflicts", [])
    if not isinstance(conflicts, list):
        return False
    return any(
        isinstance(conflict, dict) and conflict.get("code") == "cargo_un_conflict"
        for conflict in conflicts
    )


def _has_rejected_field(request: ValidatedShipmentRequest, field: str) -> bool:
    rejected = request.inferred_flags.get("rejected_fields", [])
    if not isinstance(rejected, list):
        return False
    return any(
        isinstance(issue, dict) and issue.get("field") == field
        for issue in rejected
    )


def _append_missing_profile_field(
    request: ValidatedShipmentRequest,
    target: list[str],
    profile_name: str,
    field_name: str,
    label: str,
) -> None:
    profile = request.profiles.get(profile_name, {})
    if not isinstance(profile, dict) or profile.get(field_name) is None:
        target.append(label)


def _without_existing(values: list[str], existing: list[str]) -> list[str]:
    existing_set = set(existing)
    return [value for value in values if value not in existing_set]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
