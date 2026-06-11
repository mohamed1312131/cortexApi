from __future__ import annotations


def explain_intake_term(message: str) -> str:
    text = message.lower()
    if "state of charge" in text or "state-of-charge" in text or "soc" in text:
        return (
            "The state of charge matters mainly for lithium battery air preparation. "
            "It can affect dangerous-goods handling and carrier review, so it is asked "
            "during intake rather than guessed. If you do not know it, I will keep it "
            "marked as unknown instead of assuming a value."
        )
    if "battery packing" in text or "packing configuration" in text:
        return (
            "Battery packing configuration describes how the lithium batteries travel: "
            "shipped alone, packed with equipment, or contained in equipment. It changes "
            "which dangerous-goods preparation applies. Answer with whichever matches your "
            "shipment; if you are not sure yet, I will keep it marked as unknown."
        )
    if "un38.3" in text or "un 38.3" in text:
        return (
            "UN38.3 is test evidence for lithium batteries. It helps carriers confirm "
            "the battery design has passed required transport safety tests. If you do "
            "not have it yet, I will keep it marked as unknown rather than assume it."
        )
    if "un number" in text or "un " in text:
        return (
            "A UN number is a 4-digit dangerous-goods identifier, such as UN3480 "
            "for lithium ion batteries. It tells the next layers which transport "
            "rules and restrictions to check. If you do not know it, I will keep it "
            "marked as unknown instead of guessing one."
        )
    if "packing group" in text:
        return (
            "A packing group indicates the relative hazard level of dangerous goods "
            "(I, II, or III). It influences which handling and documentation rules the "
            "next layers check. If you do not know it, I will keep it marked as unknown."
        )
    if "hs code" in text:
        return (
            "An HS code is a customs commodity code. It helps with customs, duties, "
            "and document preparation, but it should not be guessed."
        )
    if "incoterm" in text:
        return (
            "An Incoterm defines buyer and seller responsibilities, such as EXW, FOB, "
            "CIF, or DAP. It can affect documents, costs, and handoff points."
        )
    if "sds" in text:
        return (
            "An SDS is a Safety Data Sheet. For hazardous or chemical cargo, it gives "
            "classification and handling details needed before transport checks."
        )
    if "placi" in text:
        return (
            "PLACI is pre-loading advance cargo information for air freight security. "
            "It affects whether minimum shipment data is ready before air loading."
        )
    return (
        "That term affects intake quality, but it should not be used as a guessed fact. "
        "Give it if you have it; otherwise I will keep it marked as unknown."
    )
