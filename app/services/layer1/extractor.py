# app/services/layer1/extractor.py
from __future__ import annotations
import json

from pydantic import BaseModel

from app.core.llm import get_chat_model
from app.schemas import (
    ValidatedShipmentRequest,
    CoreShipment,
    Lane,
    ModeSelection,
    CargoFlags,
    UserGoal,
    MissingFields,
    RequestedMode,
    FlagState,
    Priority,
)


# ---- city -> country resolution (code's job, not the LLM's) ----
# Small for now; later this becomes a real geocoder/gazetteer lookup.
CITY_TO_COUNTRY: dict[str, str] = {
    "milan": "IT",
    "paris": "FR",
    "lyon": "FR",
    "shenzhen": "CN",
}


# ---- the small intermediate shape the LLM fills (NOT the contract) ----
class IntakeExtraction(BaseModel):
    cargo_description: str | None = None
    weight_kg: float | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    requested_mode: RequestedMode = RequestedMode.unknown
    dangerous_goods: FlagState = FlagState.unknown
    un_number: str | None = None


# ---- defensive content extraction (handles Gemma thinking blocks) ----
def extract_text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in {"thinking", "reasoning"}:
                    continue
                if part.get("type") in {"text", "output_text"} and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
        return "\n".join(text_parts).strip()
    return str(content).strip()


# ---- prefer the provider's clean .text accessor, fall back to block parsing ----
def extract_model_text(raw: object) -> str:
    text = getattr(raw, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    content = getattr(raw, "content", raw)
    return extract_text_content(content)


# ---- strip markdown fences if the model wraps JSON in ```json ... ``` ----
def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


# ---- the extraction prompt (LLM's job is SMALL) ----
EXTRACTION_PROMPT = """You are a logistics intake parser. Read the shipment request below and extract ONLY these fields as JSON. Return nothing but the JSON object — no explanation, no markdown.

Fields:
- cargo_description: short description of the goods, or null
- weight_kg: total weight in KILOGRAMS as a number (convert tons: 1 ton = 1000 kg), or null
- origin_city: the origin city name, or null
- destination_city: the destination city name, or null
- requested_mode: one of "sea", "air", "road", or "unknown" if not stated
- dangerous_goods: "yes" if clearly hazardous, "likely" if probably (e.g. lithium batteries, chemicals), "no" if clearly safe, "unknown" if unclear
- un_number: the UN dangerous-goods number if stated (e.g. "UN3480"), or null

Shipment request:
{message}

JSON:"""


def _call_llm(message: str) -> dict:
    model = get_chat_model(intake=True)
    if model is None:
        raise RuntimeError("No LLM configured (LLM_PROVIDER=none). Layer 1 requires an LLM.")
    raw = model.invoke(EXTRACTION_PROMPT.format(message=message))
    text = _strip_code_fences(extract_model_text(raw))
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned non-JSON intake output: {text}") from e


# ---- assemble the full contract from the small extraction (CODE's job) ----
def _build_request(case_id: str, ext: IntakeExtraction) -> ValidatedShipmentRequest:
    origin_country = CITY_TO_COUNTRY.get(ext.origin_city.lower()) if ext.origin_city else None
    destination_country = CITY_TO_COUNTRY.get(ext.destination_city.lower()) if ext.destination_city else None

    lane = Lane(
        origin_raw=ext.origin_city,
        destination_raw=ext.destination_city,
        origin_city=ext.origin_city,
        destination_city=ext.destination_city,
        origin_country=origin_country,
        destination_country=destination_country,
    )

    core = CoreShipment(
        cargo_description=ext.cargo_description,
        weight_kg=ext.weight_kg,
    )

    if ext.requested_mode is RequestedMode.unknown:
        candidates = [RequestedMode.sea, RequestedMode.air, RequestedMode.road]
        needs_selection = True
    else:
        candidates = [ext.requested_mode]
        needs_selection = False
    mode = ModeSelection(
        requested_mode=ext.requested_mode,
        candidate_modes=candidates,
        needs_mode_selection=needs_selection,
    )

    # cargo flags + profiles — code decides, from the LLM's dangerous_goods signal
    flags = CargoFlags(dangerous_goods=ext.dangerous_goods)
    active_profiles: list[str] = []
    profiles: dict = {}
    inferred: dict = {}
    if ext.dangerous_goods in (FlagState.yes, FlagState.likely):
        active_profiles.append("dangerous_goods")
        profiles["dangerous_goods"] = {"un_number": ext.un_number}
        inferred["dangerous_goods"] = {
            "value": ext.dangerous_goods.value,
            "basis": "LLM intake classification",
            "confirmed_by_user": ext.dangerous_goods is FlagState.yes,
        }

    # blocking missing fields
    blocking: list[str] = []
    if ext.weight_kg is None:
        blocking.append("weight")
    if origin_country is None or destination_country is None:
        blocking.append("origin/destination resolution")
    if ext.dangerous_goods in (FlagState.yes, FlagState.likely) and ext.un_number is None:
        blocking.append("UN number")

    # facts the user actually gave
    facts: dict = {}
    for name, value in (
        ("cargo_description", ext.cargo_description),
        ("weight_kg", ext.weight_kg),
        ("origin_city", ext.origin_city),
        ("destination_city", ext.destination_city),
    ):
        if value is not None:
            facts[name] = value

    return ValidatedShipmentRequest(
        case_id=case_id,
        user_goal=UserGoal(primary_goal="find_preparation_paths", priority=Priority.unknown),
        core_shipment=core,
        lane=lane,
        mode=mode,
        cargo_flags=flags,
        active_profiles=active_profiles,
        profiles=profiles,
        facts_from_user=facts,
        inferred_flags=inferred,
        missing_fields=MissingFields(blocking=blocking),
        # Slice behavior: keep True so Layer 2 can surface gates/unknowns.
        # Production behavior: compute from missing_fields.blocking.
        ready_for_layer_2=True,
    )


# ---- public entry point ----
def extract_shipment(case_id: str, message: str) -> ValidatedShipmentRequest:
    """
    Layer 1 intake: shipment sentence -> ValidatedShipmentRequest.
    The LLM fills a small flat IntakeExtraction; code assembles and validates the contract.
    """
    raw = _call_llm(message)
    ext = IntakeExtraction.model_validate(raw)   # gate 1: LLM's flat output valid
    return _build_request(case_id, ext)          # gate 2: contract valid on construction