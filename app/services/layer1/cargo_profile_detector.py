from __future__ import annotations

import re

from app.schemas import FlagState, ValidatedShipmentRequest


def apply_cargo_profiles(request: ValidatedShipmentRequest) -> ValidatedShipmentRequest:
    updated = request.model_copy(deep=True)
    text = _cargo_text(updated)

    if _has_lithium_battery_evidence(text):
        _activate(updated, "dangerous_goods")
        _activate(updated, "lithium_battery")
        un_number = _find_un_number(updated)
        updated.cargo_flags.dangerous_goods = FlagState.yes if un_number else _raise_flag(
            updated.cargo_flags.dangerous_goods
        )
        dg_profile = updated.profiles.setdefault("dangerous_goods", {})
        if un_number and not dg_profile.get("un_number"):
            dg_profile["un_number"] = un_number
        lithium = updated.profiles.setdefault("lithium_battery", {})
        lithium.setdefault("battery_type", None)
        lithium.setdefault("packed_with_equipment", None)
        lithium.setdefault("state_of_charge_pct", None)
        lithium.setdefault("un38_3_available", None)
        updated.inferred_flags["dangerous_goods"] = {
            "value": updated.cargo_flags.dangerous_goods.value,
            "basis": "cargo description or UN number indicates lithium battery cargo",
            "confirmed_by_user": updated.cargo_flags.dangerous_goods is FlagState.yes,
        }

    if _has_any(text, ("chemical", "flammable", "hazardous", "dangerous goods", "aerosol", "paint")):
        _activate(updated, "dangerous_goods")
        un_number = _find_un_number(updated)
        updated.cargo_flags.dangerous_goods = FlagState.yes if un_number else _raise_flag(
            updated.cargo_flags.dangerous_goods
        )
        dg = updated.profiles.setdefault("dangerous_goods", {})
        if un_number and not dg.get("un_number"):
            dg["un_number"] = un_number
        dg.setdefault("hazard_class", None)
        dg.setdefault("packing_group", None)
        dg.setdefault("sds_available", None)

    if _has_any(text, ("unknown chemical", "chemical sample", "lab sample")):
        _activate(updated, "unknown_chemical")
        updated.profiles.setdefault("unknown_chemical", {}).setdefault("sds_available", None)

    if _has_any(text, ("vaccine", "vaccines", "pharma", "medicine", "medicines", "medical")):
        _activate(updated, "pharma")
        _activate(updated, "temperature_controlled")
        updated.cargo_flags.pharma = _raise_flag(updated.cargo_flags.pharma)
        updated.cargo_flags.temperature_controlled = _raise_flag(updated.cargo_flags.temperature_controlled)
        pharma = updated.profiles.setdefault("pharma", {})
        pharma.setdefault("temperature_range", None)
        pharma.setdefault("shelf_life", None)
        pharma.setdefault("cold_chain_required", None)
        temp = updated.profiles.setdefault("temperature_controlled", {})
        temp.setdefault("temperature_range", None)
        temp.setdefault("packaging_type", None)

    if _has_any(text, ("frozen", "chilled", "refrigerated", "reefer", "fresh fish", "fish", "meat", "food")):
        _activate(updated, "food_perishable")
        _activate(updated, "temperature_controlled")
        updated.cargo_flags.food_perishable = _raise_flag(updated.cargo_flags.food_perishable)
        updated.cargo_flags.temperature_controlled = _raise_flag(updated.cargo_flags.temperature_controlled)
        temp = updated.profiles.setdefault("temperature_controlled", {})
        temp.setdefault("temperature_range", None)
        temp.setdefault("packaging_type", None)
        temp.setdefault("shelf_life", None)
        updated.profiles.setdefault("food_perishable", {}).setdefault("shelf_life", None)

    if _has_any(text, ("oversized", "out of gauge", "heavy lift", "industrial machine", "machinery", "machine")):
        _activate(updated, "oversized")
        updated.cargo_flags.oversized = _raise_flag(updated.cargo_flags.oversized)
        oversized = updated.profiles.setdefault("oversized", {})
        oversized.setdefault("single_piece_weight_kg", None)
        oversized.setdefault("stackable", None)
        oversized.setdefault("lifting_points", None)
        if updated.core_shipment.weight_kg and updated.core_shipment.weight_kg >= 20000:
            _activate(updated, "heavy_lift")

    if _has_any(text, ("gold", "jewelry", "jewellery", "high value", "luxury", "electronics")):
        _activate(updated, "high_value")
        updated.cargo_flags.high_value = _raise_flag(updated.cargo_flags.high_value)
        updated.profiles.setdefault("high_value", {}).setdefault("cargo_value", updated.commercial.cargo_value)

    if _has_any(text, ("live animal", "live animals", "livestock")):
        _activate(updated, "live_animals")
        updated.cargo_flags.live_animals = _raise_flag(updated.cargo_flags.live_animals)
        live = updated.profiles.setdefault("live_animals", {})
        live.setdefault("species", None)
        live.setdefault("health_documents_available", None)

    if _has_any(text, ("vehicle", "car", "cars", "truck", "motorcycle")):
        _activate(updated, "vehicle")
        updated.profiles.setdefault("vehicle", {}).setdefault("fuel_status", None)

    if _has_any(text, ("liquid bulk", "bulk liquid", "tank")):
        _activate(updated, "liquid_bulk")
        updated.profiles.setdefault("liquid_bulk", {}).setdefault("tank_required", None)

    if _has_any(text, ("dry bulk", "bulk grain", "cement bulk")):
        _activate(updated, "dry_bulk")
        updated.profiles.setdefault("dry_bulk", {}).setdefault("bulk_equipment_required", None)

    if _has_any(text, ("container", "containerized", "fcl", "lcl")):
        _activate(updated, "containerized")
        updated.profiles.setdefault("containerized", {}).setdefault("load_type", None)

    if updated.core_shipment.cargo_description and not updated.active_profiles:
        _activate(updated, "general_cargo")

    updated.active_profiles = _dedupe(updated.active_profiles)
    return updated


def _cargo_text(request: ValidatedShipmentRequest) -> str:
    parts = [
        request.core_shipment.cargo_description or "",
        str(request.facts_from_user.get("cargo_description") or ""),
        str(_find_un_number(request) or ""),
    ]
    return " ".join(parts).lower()


def _find_un_number(request: ValidatedShipmentRequest) -> str | None:
    for profile in request.profiles.values():
        if isinstance(profile, dict) and profile.get("un_number"):
            value = str(profile["un_number"]).upper().replace(" ", "")
            if re.fullmatch(r"UN\d{4}", value):
                return value
    value = request.facts_from_user.get("un_number")
    if not value:
        return None
    normalized = str(value).upper().replace(" ", "")
    return normalized if re.fullmatch(r"UN\d{4}", normalized) else None


def _activate(request: ValidatedShipmentRequest, profile: str) -> None:
    if profile not in request.active_profiles:
        request.active_profiles.append(profile)
    request.profiles.setdefault(profile, {})


def _raise_flag(current: FlagState) -> FlagState:
    if current is FlagState.yes:
        return FlagState.yes
    return FlagState.likely


_LITHIUM_UN_NUMBERS = {"un3480", "un3481", "un3090", "un3091"}


def _has_lithium_battery_evidence(text: str) -> bool:
    has_lithium_marker = (
        _contains_term(text, "lithium")
        or any(_contains_term(text, un) for un in _LITHIUM_UN_NUMBERS)
    )
    has_battery_marker = (
        _contains_term(text, "battery")
        or _contains_term(text, "batteries")
        or any(_contains_term(text, un) for un in _LITHIUM_UN_NUMBERS)
    )
    return has_lithium_marker and has_battery_marker


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(_contains_term(text, needle) for needle in needles)


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", text.lower()) is not None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
