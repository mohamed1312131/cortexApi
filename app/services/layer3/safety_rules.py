# app/services/layer3/safety_rules.py
from __future__ import annotations

import re

from app.schemas.layer3 import AnalystDraft, DeterministicDecision, ReasoningContext

# Shared Layer 3 safety vocabulary + helpers.
#
# Used by BOTH the Analyst Agent's own output contract check and the authoritative
# Safety Gate. Centralised here so the two never drift. No LLM, no I/O.


# Phrases the Analyst (and any Layer 3 narrative) must never use: Layer 3 does not
# approve, clear, confirm, or guarantee anything.
FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "approved",
    "compliant",
    "guaranteed",
    "carrier accepted",
    "customs cleared",
    "booking confirmed",
    "final legal clearance",
    "final customs clearance",
    "final carrier approval",
)

# Internal scoring vocabulary that must never leak into a narrative.
RAW_SCORE_TOKENS: tuple[str, ...] = (
    "raw_score",
    "raw_scores_by_path",
    "internal_scoring_trace",
    "internalscoringtrace",
    "internal score",
    "score:",
)

# Numeric percentages are a common form of leaked raw score; Layer 3 narratives
# are banded, never numeric.
PERCENTAGE_RE = re.compile(r"\d+(?:\.\d+)?\s*%")


def contains_forbidden_claim(text: str) -> list[str]:
    """Return the forbidden claim phrases found in ``text`` (case-insensitive)."""
    lowered = text.lower()
    return [phrase for phrase in FORBIDDEN_CLAIMS if phrase in lowered]


def contains_raw_score_leakage(text: str) -> list[str]:
    """Return raw-score leakage markers found in ``text`` (tokens + percentage)."""
    lowered = text.lower()
    found = [token for token in RAW_SCORE_TOKENS if token in lowered]
    if PERCENTAGE_RE.search(text):
        found.append("percentage")
    return found


def allowed_evidence_refs(
    context: ReasoningContext, decision: DeterministicDecision
) -> set[str]:
    """The full set of evidence refs a narrative is permitted to cite."""
    allowed: set[str] = set()
    for path in decision.ranked_path_families:
        allowed.update(path.evidence_refs)
    for ref in context.evidence_refs:
        allowed.add(ref.ref_id)
    for factor in (
        list(context.hard_gates)
        + list(context.unknowns)
        + list(context.missing_fields)
        + list(context.conflicts)
    ):
        allowed.update(factor.evidence_refs)
    return allowed


def analyst_draft_text(draft: AnalystDraft) -> str:
    """Join every free-text field of an AnalystDraft into one scannable blob."""
    parts: list[str] = [
        draft.overall_summary,
        draft.next_action_summary or "",
        draft.dispute_reason or "",
    ]
    parts.extend(draft.user_clarification_questions)
    parts.extend(draft.layer2_refetch_requests)
    for narrative in draft.narratives:
        parts.append(narrative.why_ranked_here)
        parts.append(narrative.why_not_higher)
        parts.extend(narrative.what_would_improve_readiness)
    return " ".join(parts)
