from __future__ import annotations

import re

from app.schemas import Priority, ValidatedShipmentRequest
from app.services.layer1.extractor import CITY_TO_COUNTRY, COUNTRY_TO_ISO


INCOTERMS = {
    "EXW",
    "FCA",
    "FAS",
    "FOB",
    "CFR",
    "CIF",
    "CPT",
    "CIP",
    "DAP",
    "DPU",
    "DDP",
}


def apply_deterministic_updates(
    request: ValidatedShipmentRequest,
    message: str,
) -> tuple[ValidatedShipmentRequest, list[str]]:
    updated = request.model_copy(deep=True)
    changed_fields: list[str] = []
    text = " ".join(message.strip().split())
    lower = text.lower()

    _apply_un_number(updated, text, changed_fields)
    _apply_weight(updated, lower, changed_fields)
    _apply_volume(updated, lower, changed_fields)
    _apply_dimensions(updated, lower, changed_fields)
    _apply_incoterm(updated, text, changed_fields)
    _apply_priority(updated, lower, changed_fields)
    _apply_lane_updates(updated, text, lower, changed_fields)
    _apply_lithium_battery_followups(updated, lower, changed_fields)

    return updated, _dedupe(changed_fields)


def _apply_lithium_battery_followups(
    request: ValidatedShipmentRequest,
    lower: str,
    changed_fields: list[str],
) -> None:
    # Only interpret these answers when a lithium battery profile is already active,
    # i.e. the user is answering a battery question Layer 1 previously asked.
    if "lithium_battery" not in request.active_profiles:
        return
    profile = request.profiles.setdefault("lithium_battery", {})
    if not isinstance(profile, dict):
        return

    soc = _state_of_charge_pct(lower)
    if soc is not None and profile.get("state_of_charge_pct") != soc:
        profile["state_of_charge_pct"] = soc
        request.facts_from_user["state_of_charge_pct"] = soc
        request.field_confidence["state_of_charge_pct"] = 0.9
        changed_fields.append("profiles.lithium_battery.state_of_charge_pct")

    packing = _battery_packing_config(lower)
    if packing is not None and profile.get("packed_with_equipment") != packing:
        profile["packed_with_equipment"] = packing
        request.facts_from_user["packed_with_equipment"] = packing
        request.field_confidence["packed_with_equipment"] = 0.9
        changed_fields.append("profiles.lithium_battery.packed_with_equipment")


def _state_of_charge_pct(lower: str) -> float | None:
    if not any(term in lower for term in ("state of charge", "state-of-charge", "soc")):
        return None
    match = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", lower)
    if not match:
        match = re.search(
            r"(?:state of charge|state-of-charge|soc)\D{0,15}(\d{1,3}(?:[.,]\d+)?)",
            lower,
        )
    if not match:
        return None
    value = _to_float(match.group(1))
    if value is None or not 0 <= value <= 100:
        return None
    return value


def _battery_packing_config(lower: str) -> str | None:
    if any(
        term in lower
        for term in ("contained in equipment", "contained in the equipment", "inside equipment", "in the equipment")
    ):
        return "contained_in_equipment"
    if any(term in lower for term in ("packed with equipment", "with equipment", "with the equipment")):
        return "packed_with_equipment"
    if any(
        term in lower
        for term in ("shipped alone", "alone", "by themselves", "on their own", "batteries only", "standalone")
    ):
        return "alone"
    return None


def _apply_un_number(
    request: ValidatedShipmentRequest,
    text: str,
    changed_fields: list[str],
) -> None:
    match = re.search(r"\bUN\s?(\d{4})\b", text, flags=re.IGNORECASE)
    if not match:
        return
    value = f"UN{match.group(1)}"
    profile = request.profiles.setdefault("dangerous_goods", {})
    if isinstance(profile, dict) and profile.get("un_number") != value:
        profile["un_number"] = value
        changed_fields.append("profiles.dangerous_goods.un_number")
    request.facts_from_user["un_number"] = value
    request.field_confidence["un_number"] = max(request.field_confidence.get("un_number", 0), 0.95)


def _apply_weight(
    request: ValidatedShipmentRequest,
    lower: str,
    changed_fields: list[str],
) -> None:
    if re.search(r"(?<!\w)-\s*\d+(?:[.,]\d+)?\s*(kg|kilograms?|tons?|tonnes?|t)\b", lower):
        return
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(kg|kilograms?|tons?|tonnes?|t)\b", lower)
    if not match:
        return
    number = _to_float(match.group(1))
    unit = match.group(2)
    if number is None:
        return
    value = number * 1000 if unit in {"ton", "tons", "tonne", "tonnes", "t"} else number
    if value > 0 and request.core_shipment.weight_kg != value:
        request.core_shipment.weight_kg = value
        request.facts_from_user["weight_kg"] = value
        request.field_confidence["weight_kg"] = 0.95
        changed_fields.append("core_shipment.weight_kg")


def _apply_volume(
    request: ValidatedShipmentRequest,
    lower: str,
    changed_fields: list[str],
) -> None:
    if re.search(r"(?<!\w)-\s*\d+(?:[.,]\d+)?\s*(cbm|m3|cubic meters?|cubic metres?)\b", lower):
        return
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(cbm|m3|cubic meters?|cubic metres?)\b", lower)
    if not match:
        return
    value = _to_float(match.group(1))
    if value and value > 0 and request.core_shipment.volume_cbm != value:
        request.core_shipment.volume_cbm = value
        request.facts_from_user["volume_cbm"] = value
        request.field_confidence["volume_cbm"] = 0.95
        changed_fields.append("core_shipment.volume_cbm")


def _apply_dimensions(
    request: ValidatedShipmentRequest,
    lower: str,
    changed_fields: list[str],
) -> None:
    match = re.search(
        r"\b(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)(?:\s*(m|cm))?\b",
        lower,
    )
    if not match:
        return
    values = [_to_float(match.group(i)) for i in range(1, 4)]
    if any(value is None or value <= 0 for value in values):
        return
    unit = match.group(4)
    dimensions = [value / 100 for value in values] if unit == "cm" else values
    if request.core_shipment.dimensions != dimensions:
        request.core_shipment.dimensions = dimensions
        request.facts_from_user["dimensions"] = dimensions
        request.field_confidence["dimensions"] = 0.92
        changed_fields.append("core_shipment.dimensions")


def _apply_incoterm(
    request: ValidatedShipmentRequest,
    text: str,
    changed_fields: list[str],
) -> None:
    match = re.search(r"\b(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b", text, flags=re.IGNORECASE)
    if not match:
        return
    value = match.group(1).upper()
    if request.commercial.incoterm != value:
        request.commercial.incoterm = value
        request.facts_from_user["incoterm"] = value
        request.field_confidence["incoterm"] = 0.95
        changed_fields.append("commercial.incoterm")


def _apply_priority(
    request: ValidatedShipmentRequest,
    lower: str,
    changed_fields: list[str],
) -> None:
    priority = Priority.unknown
    if any(term in lower for term in ("cheapest", "lowest cost", "cost priority", "budget")):
        priority = Priority.cost
    elif any(term in lower for term in ("fastest", "urgent", "speed priority", "asap")):
        priority = Priority.speed
    elif any(term in lower for term in ("lowest risk", "risk priority")):
        priority = Priority.risk
    elif any(term in lower for term in ("compliance priority", "compliance first")):
        priority = Priority.compliance
    elif "balanced" in lower:
        priority = Priority.balanced

    if priority is not Priority.unknown and request.user_goal.priority != priority:
        request.user_goal.priority = priority
        request.facts_from_user["priority"] = priority.value
        request.field_confidence["priority"] = 0.85
        changed_fields.append("user_goal.priority")


def _apply_lane_updates(
    request: ValidatedShipmentRequest,
    text: str,
    lower: str,
    changed_fields: list[str],
) -> None:
    from_to = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:[.!?]|$)", text, flags=re.IGNORECASE)
    if from_to:
        _set_origin(request, _clean_location(from_to.group(1)), changed_fields)
        _set_destination(request, _clean_location(from_to.group(2)), changed_fields)
        return

    origin = re.search(r"\b(?:origin|pickup|pick-up)\s+(?:is|=|:)?\s*([A-Za-z][A-Za-z .'-]+)", text, flags=re.IGNORECASE)
    if origin:
        _set_origin(request, _clean_location(origin.group(1)), changed_fields)

    destination = re.search(
        r"\b(?:destination|deliver(?:y)?\s+to|to)\s+(?:is|=|:)?\s*([A-Za-z][A-Za-z .'-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if destination:
        _set_destination(request, _clean_location(destination.group(1)), changed_fields)


def _set_origin(
    request: ValidatedShipmentRequest,
    location: str,
    changed_fields: list[str],
) -> None:
    if not location:
        return
    country = _country_for_location(location)
    city = location if location.lower() in CITY_TO_COUNTRY else None
    _set_if_changed(request.lane, "origin_raw", location, "lane.origin_raw", changed_fields)
    request.field_confidence["origin_raw"] = 0.9
    if city:
        _set_if_changed(request.lane, "origin_city", city, "lane.origin_city", changed_fields)
        request.facts_from_user["origin_city"] = city
        request.field_confidence["origin_city"] = 0.9
    if country:
        _set_if_changed(request.lane, "origin_country", country, "lane.origin_country", changed_fields)
        request.facts_from_user["origin_country"] = country
        request.field_confidence["origin_country"] = 0.9
    request.facts_from_user["origin_raw"] = location


def _set_destination(
    request: ValidatedShipmentRequest,
    location: str,
    changed_fields: list[str],
) -> None:
    if not location:
        return
    country = _country_for_location(location)
    city = location if location.lower() in CITY_TO_COUNTRY else None
    _set_if_changed(request.lane, "destination_raw", location, "lane.destination_raw", changed_fields)
    request.field_confidence["destination_raw"] = 0.9
    if city:
        _set_if_changed(request.lane, "destination_city", city, "lane.destination_city", changed_fields)
        request.facts_from_user["destination_city"] = city
        request.field_confidence["destination_city"] = 0.9
    if country:
        _set_if_changed(request.lane, "destination_country", country, "lane.destination_country", changed_fields)
        request.facts_from_user["destination_country"] = country
        request.field_confidence["destination_country"] = 0.9
    request.facts_from_user["destination_raw"] = location


def _set_if_changed(
    obj: object,
    field_name: str,
    value: object,
    changed_field: str,
    changed_fields: list[str],
) -> None:
    if getattr(obj, field_name) != value:
        setattr(obj, field_name, value)
        changed_fields.append(changed_field)


def _country_for_location(location: str) -> str | None:
    normalized = location.strip().lower()
    if normalized in COUNTRY_TO_ISO:
        return COUNTRY_TO_ISO[normalized]
    if normalized in CITY_TO_COUNTRY:
        return CITY_TO_COUNTRY[normalized]
    if len(location.strip()) == 2:
        return location.strip().upper()
    return None


def _clean_location(value: str) -> str:
    cleaned = value.strip(" ,.;:!?")
    cleaned = re.sub(r"\b(by|with|using|for|and|it|is)\b.*$", "", cleaned, flags=re.IGNORECASE).strip()
    return " ".join(part.capitalize() for part in cleaned.split())


def _to_float(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
