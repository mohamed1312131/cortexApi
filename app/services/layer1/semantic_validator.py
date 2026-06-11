from __future__ import annotations

import re

from app.schemas import FlagState, RequestedMode, ValidatedShipmentRequest
from app.services.layer1.deterministic_update_extractor import INCOTERMS
from app.services.layer1.extractor import COUNTRY_TO_ISO


CARGO_UN_CONFLICT_BLOCKER = "cargo / UN number conflict clarification"
_CARGO_UN_CONFLICT_CODE = "cargo_un_conflict"
_LITHIUM_UN_NUMBERS = {"UN3480", "UN3481", "UN3090", "UN3091"}
_NON_LITHIUM_TERMS = (
    "perfume",
    "fragrance",
    "textile",
    "textiles",
    "garment",
    "garments",
    "cosmetic",
    "cosmetics",
)
_LITHIUM_TERMS = ("lithium", "battery", "batteries", "cell", "cells", "equipment")


def normalize_and_validate_request(
    request: ValidatedShipmentRequest,
) -> tuple[ValidatedShipmentRequest, list[str]]:
    updated = request.model_copy(deep=True)
    changed_fields: list[str] = []
    warnings: list[dict[str, str]] = []

    _normalize_country_codes(updated, changed_fields)
    _validate_positive_number(updated, "core_shipment", "weight_kg", "weight_kg", changed_fields, warnings)
    _validate_positive_number(updated, "core_shipment", "volume_cbm", "volume_cbm", changed_fields, warnings)
    _validate_positive_number(updated, "commercial", "cargo_value", "cargo_value", changed_fields, warnings)
    _validate_quantity(updated, changed_fields, warnings)
    _validate_dimensions(updated, changed_fields, warnings)
    _validate_un_number(updated, changed_fields, warnings)
    _normalize_incoterm(updated, changed_fields, warnings)
    _normalize_lane_raw_from_city(updated, changed_fields)
    _normalize_mode(updated, changed_fields)

    if warnings:
        updated.inferred_flags["validation_warnings"] = warnings
    else:
        updated.inferred_flags.pop("validation_warnings", None)

    return updated, _dedupe(changed_fields)


def detect_cargo_un_conflict(request: ValidatedShipmentRequest) -> list[dict[str, str]]:
    un_number = _dangerous_goods_un_number(request)
    if un_number not in _LITHIUM_UN_NUMBERS:
        return []

    cargo = request.core_shipment.cargo_description or ""
    if not _cargo_is_clearly_non_lithium(cargo):
        return []

    return [
        {
            "code": _CARGO_UN_CONFLICT_CODE,
            "field": "profiles.dangerous_goods.un_number",
            "cargo_description": cargo,
            "un_number": un_number,
            "reason": f"{un_number} is associated with lithium ion batteries, but cargo is {cargo}",
        }
    ]


def apply_cargo_un_conflict_validation(
    request: ValidatedShipmentRequest,
) -> tuple[ValidatedShipmentRequest, list[str]]:
    updated = request.model_copy(deep=True)
    changed_fields: list[str] = []
    _clear_cargo_un_conflicts(updated)

    conflicts = detect_cargo_un_conflict(updated)
    if not conflicts:
        changed_fields.extend(_cleanup_stale_lithium_profile(updated))
        return updated, changed_fields

    changed_fields.extend(_remove_profile(updated, "lithium_battery"))

    if updated.cargo_flags.dangerous_goods is not FlagState.likely:
        updated.cargo_flags.dangerous_goods = FlagState.likely
        changed_fields.append("cargo_flags.dangerous_goods")
    updated.inferred_flags["dangerous_goods"] = {
        "value": FlagState.likely.value,
        "basis": "UN number conflicts with cargo description; awaiting user clarification",
        "confirmed_by_user": False,
    }

    updated.inferred_flags["validation_conflicts"] = conflicts
    warnings = updated.inferred_flags.setdefault("validation_warnings", [])
    if isinstance(warnings, list):
        warnings.extend(conflicts)
    else:
        updated.inferred_flags["validation_warnings"] = conflicts

    changed_fields.append("validation_conflicts")
    return updated, _dedupe(changed_fields)


def _normalize_country_codes(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
) -> None:
    for field_name in ("origin_country", "destination_country"):
        value = getattr(request.lane, field_name)
        if value is None:
            continue
        normalized = value.upper()
        if len(normalized) != 2:
            setattr(request.lane, field_name, None)
            request.facts_from_user.pop(field_name, None)
            changed_fields.append(f"lane.{field_name}")
        elif value != normalized:
            setattr(request.lane, field_name, normalized)
            request.facts_from_user[field_name] = normalized
            changed_fields.append(f"lane.{field_name}")


def _validate_positive_number(
    request: ValidatedShipmentRequest,
    group_name: str,
    field_name: str,
    fact_name: str,
    changed_fields: list[str],
    warnings: list[dict[str, str]],
) -> None:
    group = getattr(request, group_name)
    value = getattr(group, field_name)
    if value is None:
        return
    if value <= 0:
        setattr(group, field_name, None)
        request.facts_from_user.pop(fact_name, None)
        request.field_confidence.pop(fact_name, None)
        changed_fields.append(f"{group_name}.{field_name}")
        issue = {
            "field": f"{group_name}.{field_name}",
            "value": str(value),
            "reason": "value must be positive",
        }
        warnings.append(issue)
        rejected = request.inferred_flags.setdefault("rejected_fields", [])
        if isinstance(rejected, list):
            rejected.append(issue)


def _validate_quantity(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
    warnings: list[dict[str, str]],
) -> None:
    value = request.core_shipment.quantity
    if value is None:
        return
    if value <= 0:
        request.core_shipment.quantity = None
        request.facts_from_user.pop("quantity", None)
        request.field_confidence.pop("quantity", None)
        changed_fields.append("core_shipment.quantity")
        issue = {
            "field": "core_shipment.quantity",
            "value": str(value),
            "reason": "quantity must be positive",
        }
        warnings.append(issue)
        rejected = request.inferred_flags.setdefault("rejected_fields", [])
        if isinstance(rejected, list):
            rejected.append(issue)


def _validate_dimensions(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
    warnings: list[dict[str, str]],
) -> None:
    value = request.core_shipment.dimensions
    if value is None:
        return
    if len(value) != 3 or any(dimension <= 0 for dimension in value):
        request.core_shipment.dimensions = None
        request.facts_from_user.pop("dimensions", None)
        request.field_confidence.pop("dimensions", None)
        changed_fields.append("core_shipment.dimensions")
        warnings.append(
            {
                "field": "core_shipment.dimensions",
                "reason": "dimensions must be exactly three positive numbers",
            }
        )


def _validate_un_number(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
    warnings: list[dict[str, str]],
) -> None:
    profile = request.profiles.get("dangerous_goods")
    if not isinstance(profile, dict):
        return
    value = profile.get("un_number")
    if value is None:
        return
    normalized = str(value).upper().replace(" ", "")
    if not re.fullmatch(r"UN\d{4}", normalized):
        profile["un_number"] = None
        request.facts_from_user.pop("un_number", None)
        request.field_confidence.pop("un_number", None)
        changed_fields.append("profiles.dangerous_goods.un_number")
        issue = {
            "field": "profiles.dangerous_goods.un_number",
            "value": str(value),
            "reason": "UN numbers must have 4 digits",
        }
        warnings.append(issue)
        rejected = request.inferred_flags.setdefault("rejected_fields", [])
        if isinstance(rejected, list):
            rejected.append(issue)
        return
    if normalized != value:
        profile["un_number"] = normalized
        request.facts_from_user["un_number"] = normalized
        changed_fields.append("profiles.dangerous_goods.un_number")


def _normalize_incoterm(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
    warnings: list[dict[str, str]],
) -> None:
    value = request.commercial.incoterm
    if value is None:
        return
    normalized = value.upper()
    if normalized not in INCOTERMS:
        request.commercial.incoterm = None
        request.facts_from_user.pop("incoterm", None)
        request.field_confidence.pop("incoterm", None)
        changed_fields.append("commercial.incoterm")
        warnings.append({"field": "commercial.incoterm", "reason": "unknown Incoterm"})
    elif value != normalized:
        request.commercial.incoterm = normalized
        request.facts_from_user["incoterm"] = normalized
        changed_fields.append("commercial.incoterm")


def _normalize_lane_raw_from_city(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
) -> None:
    _prefer_city_raw(
        request,
        raw_field="origin_raw",
        city_field="origin_city",
        country_field="origin_country",
        changed_fields=changed_fields,
    )
    _prefer_city_raw(
        request,
        raw_field="destination_raw",
        city_field="destination_city",
        country_field="destination_country",
        changed_fields=changed_fields,
    )


def _prefer_city_raw(
    request: ValidatedShipmentRequest,
    *,
    raw_field: str,
    city_field: str,
    country_field: str,
    changed_fields: list[str],
) -> None:
    city = getattr(request.lane, city_field)
    raw = getattr(request.lane, raw_field)
    country = getattr(request.lane, country_field)
    if not city:
        return
    if raw and not _raw_is_country_level(raw, country):
        return
    if raw == city:
        return
    setattr(request.lane, raw_field, city)
    request.facts_from_user[raw_field] = city
    request.field_confidence[raw_field] = max(request.field_confidence.get(raw_field, 0), 0.9)
    changed_fields.append(f"lane.{raw_field}")


def _raw_is_country_level(raw: str, country: str | None) -> bool:
    normalized = raw.strip().lower()
    if len(normalized) == 2:
        return normalized.upper() == country
    return COUNTRY_TO_ISO.get(normalized) == country


def _normalize_mode(
    request: ValidatedShipmentRequest,
    changed_fields: list[str],
) -> None:
    candidates = [mode for mode in request.mode.candidate_modes if mode is not RequestedMode.unknown]
    if not candidates:
        candidates = [RequestedMode.sea, RequestedMode.air, RequestedMode.road]
    if request.mode.requested_mode is RequestedMode.unknown:
        request.mode.needs_mode_selection = True
    elif request.mode.requested_mode not in candidates:
        candidates = [request.mode.requested_mode]
        request.mode.needs_mode_selection = False

    if request.mode.candidate_modes != candidates:
        request.mode.candidate_modes = candidates
        changed_fields.append("mode.candidate_modes")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dangerous_goods_un_number(request: ValidatedShipmentRequest) -> str | None:
    profile = request.profiles.get("dangerous_goods")
    if isinstance(profile, dict) and profile.get("un_number"):
        value = str(profile["un_number"]).upper().replace(" ", "")
        if re.fullmatch(r"UN\d{4}", value):
            return value
    value = request.facts_from_user.get("un_number")
    if not value:
        return None
    normalized = str(value).upper().replace(" ", "")
    return normalized if re.fullmatch(r"UN\d{4}", normalized) else None


def _clear_cargo_un_conflicts(request: ValidatedShipmentRequest) -> None:
    for key in ("validation_conflicts", "validation_warnings"):
        issues = request.inferred_flags.get(key)
        if not isinstance(issues, list):
            continue
        remaining = [
            issue
            for issue in issues
            if not (isinstance(issue, dict) and issue.get("code") == _CARGO_UN_CONFLICT_CODE)
        ]
        if remaining:
            request.inferred_flags[key] = remaining
        else:
            request.inferred_flags.pop(key, None)


def _cleanup_stale_lithium_profile(request: ValidatedShipmentRequest) -> list[str]:
    un_number = _dangerous_goods_un_number(request)
    cargo = request.core_shipment.cargo_description or ""
    if un_number in _LITHIUM_UN_NUMBERS or not _cargo_is_clearly_non_lithium(cargo):
        return []

    changed_fields = _remove_profile(request, "lithium_battery")
    dg_profile = request.profiles.get("dangerous_goods")
    if isinstance(dg_profile, dict) and not dg_profile.get("un_number"):
        changed_fields.extend(_remove_profile(request, "dangerous_goods"))

    if request.cargo_flags.dangerous_goods in {FlagState.yes, FlagState.likely}:
        request.cargo_flags.dangerous_goods = FlagState.no
        request.inferred_flags["dangerous_goods"] = {
            "value": FlagState.no.value,
            "basis": "cargo correction indicates non-lithium general cargo",
            "confirmed_by_user": True,
        }
        changed_fields.append("cargo_flags.dangerous_goods")

    return _dedupe(changed_fields)


def _remove_profile(request: ValidatedShipmentRequest, profile: str) -> list[str]:
    changed_fields: list[str] = []
    if profile in request.active_profiles:
        request.active_profiles = [
            active_profile for active_profile in request.active_profiles if active_profile != profile
        ]
        changed_fields.append("active_profiles")
    if profile in request.profiles:
        request.profiles.pop(profile, None)
        changed_fields.append(f"profiles.{profile}")
    return changed_fields


def _cargo_is_clearly_non_lithium(cargo: str) -> bool:
    cargo_lower = cargo.lower()
    if not cargo_lower:
        return False
    if any(term in cargo_lower for term in _LITHIUM_TERMS):
        return False
    return any(term in cargo_lower for term in _NON_LITHIUM_TERMS)
