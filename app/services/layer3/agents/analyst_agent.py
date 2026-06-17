# app/services/layer3/agents/analyst_agent.py
from __future__ import annotations

import json

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import ValidationError

from app.core.logging import get_logger
from app.core.llm import get_chat_model
from app.schemas.layer3 import AnalystDraft, DeterministicDecision, ReasoningContext
from app.services.layer3.llm_response import extract_model_text, strip_code_fences
from app.services.layer3.prompt_compaction import (
    compact_allowed_evidence_refs,
    compact_deterministic_decision_for_prompt,
    compact_path_evidence_refs,
    compact_reasoning_context_for_prompt,
)
from app.services.layer3.safety_rules import (
    analyst_draft_text,
    allowed_evidence_refs,
    contains_forbidden_claim,
    contains_raw_score_leakage,
)

# Cortex Layer 3 Analyst Agent.
#
# The Analyst EXPLAINS the DeterministicDecision. It does NOT decide readiness and
# does NOT rank path families — the deterministic engine already did that, and that
# result is fixed truth. The Analyst only narrates it using ReasoningContext
# evidence, citing evidence_refs that already exist.
#
# This module never imports or uses InternalScoringTrace or raw score values.
# Forbidden-claim / raw-score / evidence helpers are shared with the Safety Gate
# via safety_rules so the two never drift. Post-generation validation (below) is
# NOT the full safety gate; it only guards the Analyst's own output contract.

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
ANALYST_PROMPT = """You are the Cortex Layer 3 Analyst Agent.

<role>
You explain deterministic logistics readiness decisions. You do not make them.
The DeterministicDecision below is FIXED TRUTH. You may not modify ranks, modes,
readiness bands, ranking types, or path families. You only explain what is already
decided, using the evidence provided.
</role>

<rules>
1. Use ONLY the ReasoningContext and DeterministicDecision provided. Invent nothing.
2. Produce exactly one narrative per ranked path family, with the SAME rank, mode,
   and path_family as in the DeterministicDecision.
3. Cite only evidence_refs that already appear in the decision/context (the
   allowed_evidence_refs list). Never invent an evidence ref.
4. Do not change rank order, readiness_band, or path_family.
5. You must output exactly one narrative for every item in required_narratives.
   The number of narratives must equal the number of required_narratives.
6. Do not output only the best-ranked path. Do not summarize multiple paths into
   one narrative. Do not omit blocked, low-data, or lower-ranked paths.
7. Each narrative must copy rank, mode, and path_family exactly from its matching
   required_narratives item.
8. Each narrative must include non-empty evidence_refs copied from the matching
   required_narratives item.
9. For every narrative, copy the evidence_refs from the matching ranked path in
   ranked_path_evidence_refs. The matching key is exactly rank + mode + path_family.
   Do not leave evidence_refs empty.
10. Use only evidence_refs from the matching ranked path or allowed_evidence_refs.
11. If the decision cannot be honestly explained from the evidence, do NOT change it.
   Still include the matching path evidence_refs, then set disputes_ranking=true
   and populate dispute_reason.
12. Do not hide hard gates, conflicts, unknowns, or important missing fields.
13. Never say: approved, compliant, guaranteed, carrier accepted, customs cleared,
   booking confirmed, or final legal clearance.
14. Never include raw scores, percentages, or internal scoring values.
15. Do not write final customer-facing prose. This is an internal analyst draft.
16. Output ONLY one AnalystDraft JSON object. No markdown, no commentary.
</rules>

<analyst_draft_shape>
{
  "case_id": "<copy from decision>",
  "narratives": [
    {
      "path_family": "<copy>", "mode": "<copy>", "rank": <copy>,
      "why_ranked_here": "<short evidence-based explanation>",
      "why_not_higher": "<short evidence-based explanation>",
      "what_would_improve_readiness": ["<actionable item>", "..."],
      "evidence_refs": ["<subset of allowed_evidence_refs>"]
    }
  ],
  "overall_summary": "<short internal summary>",
  "next_action_summary": "<short or null>",
  "user_clarification_questions": [],
  "layer2_refetch_requests": [],
  "disputes_ranking": false,
  "dispute_reason": null,
  "forbidden_claims_used": []
}
</analyst_draft_shape>

<allowed_evidence_refs>
__ALLOWED_REFS__
</allowed_evidence_refs>

<required_narratives>
__REQUIRED_NARRATIVES__
</required_narratives>

<ranked_path_evidence_refs>
__PATH_EVIDENCE_REFS__
</ranked_path_evidence_refs>

<revision_feedback>
__REVISION_FEEDBACK__
</revision_feedback>

<reasoning_context>
__CONTEXT_JSON__
</reasoning_context>

<deterministic_decision>
__DECISION_JSON__
</deterministic_decision>

Return only the AnalystDraft JSON object.
"""


def build_analyst_prompt(
    context: ReasoningContext,
    decision: DeterministicDecision,
    revision_feedback: str | None = None,
) -> str:
    allowed = compact_allowed_evidence_refs(context, decision)
    required_narratives = [
        {
            "rank": path.rank,
            "mode": path.mode.value,
            "path_family": path.path_family,
            "readiness_band": path.readiness_band.value,
            "evidence_refs": compact_path_evidence_refs(path.evidence_refs),
        }
        for path in decision.ranked_path_families
    ]
    path_evidence_refs = [
        {
            "rank": path.rank,
            "mode": path.mode.value,
            "path_family": path.path_family,
            "evidence_refs": compact_path_evidence_refs(path.evidence_refs),
        }
        for path in decision.ranked_path_families
    ]
    feedback = (
        f"Previous output failed validation: {revision_feedback}. "
        "Fix this in the next JSON output."
        if revision_feedback
        else "No previous Analyst revision feedback."
    )
    decision_json = json.dumps(
        compact_deterministic_decision_for_prompt(decision),
        ensure_ascii=False,
        sort_keys=True,
    )
    context_json = json.dumps(
        compact_reasoning_context_for_prompt(context, decision),
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        ANALYST_PROMPT.replace("__ALLOWED_REFS__", json.dumps(allowed, ensure_ascii=False))
        .replace("__REQUIRED_NARRATIVES__", json.dumps(required_narratives, ensure_ascii=False))
        .replace("__PATH_EVIDENCE_REFS__", json.dumps(path_evidence_refs, ensure_ascii=False))
        .replace("__REVISION_FEEDBACK__", feedback)
        .replace("__CONTEXT_JSON__", context_json)
        .replace("__DECISION_JSON__", decision_json)
    )


# --------------------------------------------------------------------------- #
# model plumbing (mirrors the Layer 1 extractor style; injectable for tests)
# --------------------------------------------------------------------------- #
def _require_model(model: BaseChatModel | None) -> BaseChatModel:
    model = model or get_chat_model(layer3=True)
    if model is None:
        raise RuntimeError("No LLM configured (LLM_PROVIDER=none). Layer 3 Analyst requires an LLM.")
    return model


def _validation_summary(exc: ValidationError) -> str:
    return json.dumps(exc.errors(include_input=False), ensure_ascii=False)


def _parse_json_draft(text: str) -> AnalystDraft:
    cleaned = strip_code_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Analyst returned non-JSON output.") from exc
    try:
        return AnalystDraft.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"Analyst JSON did not match AnalystDraft: {_validation_summary(exc)}"
        ) from exc


def _to_structured_draft(result: object) -> AnalystDraft:
    if isinstance(result, AnalystDraft):
        return result
    if result is None:
        raise TypeError("none")
    if not isinstance(result, dict):
        raise TypeError(type(result).__name__)
    try:
        return AnalystDraft.model_validate(result)
    except ValidationError as exc:
        raise ValueError(
            f"Analyst structured output did not match AnalystDraft: {_validation_summary(exc)}"
        ) from exc


def _fallback_reason(result: object) -> str | None:
    if result is None:
        logger.info("analyst.structured_output_none")
        return "none"
    if not isinstance(result, (AnalystDraft, dict)):
        logger.info("analyst.structured_output_invalid_type type=%s", type(result).__name__)
        return "invalid_type"
    return None


def _parse_json_fallback(model: BaseChatModel, prompt: str, *, reason: str | None) -> AnalystDraft:
    if reason is not None:
        logger.info("analyst.json_fallback_attempted reason=%s", reason)
    try:
        raw = model.invoke(prompt)
    except Exception as exc:
        if reason is not None:
            logger.info("analyst.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise RuntimeError(
            f"Analyst model call failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    try:
        return _parse_json_draft(extract_model_text(raw))
    except ValueError as exc:
        if reason is not None:
            logger.info("analyst.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise


async def _parse_json_fallback_async(
    model: BaseChatModel,
    prompt: str,
    *,
    reason: str | None,
) -> AnalystDraft:
    if reason is not None:
        logger.info("analyst.json_fallback_attempted reason=%s", reason)
    try:
        raw = await model.ainvoke(prompt)
    except Exception as exc:
        if reason is not None:
            logger.info("analyst.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise RuntimeError(
            f"Analyst model call failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    try:
        return _parse_json_draft(extract_model_text(raw))
    except ValueError as exc:
        if reason is not None:
            logger.info("analyst.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise


def _generate_draft(model: BaseChatModel, prompt: str) -> AnalystDraft:
    """Prefer structured output; fall back to JSON parsing where unsupported."""
    fallback_reason: str | None = None
    if hasattr(model, "with_structured_output"):
        try:
            structured = model.with_structured_output(AnalystDraft)
            result = structured.invoke(prompt)
            fallback_reason = _fallback_reason(result)
            if fallback_reason is None:
                return _to_structured_draft(result)
        except NotImplementedError:
            fallback_reason = "not_implemented"
        except ValidationError as exc:
            raise ValueError(f"Analyst structured output invalid: {exc}") from exc
        except ValueError:
            raise
        except TypeError as exc:
            fallback_reason = str(exc) or "invalid_type"
        except Exception as exc:  # provider/transport failure — surface controlled
            raise RuntimeError(
                f"Analyst model call failed: {exc.__class__.__name__}: {exc}"
            ) from exc

    return _parse_json_fallback(model, prompt, reason=fallback_reason)


async def _generate_draft_async(model: BaseChatModel, prompt: str) -> AnalystDraft:
    fallback_reason: str | None = None
    if hasattr(model, "with_structured_output"):
        try:
            structured = model.with_structured_output(AnalystDraft)
            result = await structured.ainvoke(prompt)
            fallback_reason = _fallback_reason(result)
            if fallback_reason is None:
                return _to_structured_draft(result)
        except NotImplementedError:
            fallback_reason = "not_implemented"
        except ValidationError as exc:
            raise ValueError(f"Analyst structured output invalid: {exc}") from exc
        except ValueError:
            raise
        except TypeError as exc:
            fallback_reason = str(exc) or "invalid_type"
        except Exception as exc:
            raise RuntimeError(
                f"Analyst model call failed: {exc.__class__.__name__}: {exc}"
            ) from exc

    return await _parse_json_fallback_async(model, prompt, reason=fallback_reason)


# --------------------------------------------------------------------------- #
# contract validation (NOT the full safety gate)
# --------------------------------------------------------------------------- #
def _format_path_key(key: tuple) -> str:
    rank, mode, path_family = key
    return f"rank {rank} / {mode.value} / {path_family}"


def _format_path_key_lines(keys: set[tuple]) -> str:
    ordered = sorted(keys, key=lambda key: (key[0], key[1].value, key[2]))
    return "\n".join(f"- {_format_path_key(key)}" for key in ordered)


def _validate_analyst_draft_against_decision(
    draft: AnalystDraft,
    decision: DeterministicDecision,
    context: ReasoningContext,
) -> None:
    """Deterministic contract check. Raises ValueError on any violation. Never repairs."""
    if draft.case_id != decision.case_id:
        raise ValueError(
            f"Analyst case_id mismatch: draft={draft.case_id!r} decision={decision.case_id!r}"
        )

    decision_keys = {
        (p.rank, p.mode, p.path_family) for p in decision.ranked_path_families
    }
    narrative_keys = [(n.rank, n.mode, n.path_family) for n in draft.narratives]

    if len(narrative_keys) != len(set(narrative_keys)):
        raise ValueError("Analyst produced duplicate path narratives.")

    narrative_key_set = set(narrative_keys)
    missing = decision_keys - narrative_key_set
    if missing:
        raise ValueError(
            "Analyst omitted required narratives:\n"
            f"{_format_path_key_lines(missing)}"
        )
    extra = narrative_key_set - decision_keys
    if extra:
        raise ValueError(
            "Analyst added narratives not in the decision:\n"
            f"{_format_path_key_lines(extra)}"
        )

    allowed_refs = allowed_evidence_refs(context, decision)
    for narrative in draft.narratives:
        if not narrative.evidence_refs:
            raise ValueError(
                f"Analyst narrative for rank {narrative.rank} has no evidence_refs."
            )
        unknown_refs = set(narrative.evidence_refs) - allowed_refs
        if unknown_refs:
            raise ValueError(
                f"Analyst cited evidence_refs not in decision/context: {sorted(unknown_refs)}"
            )

    if draft.disputes_ranking and not (draft.dispute_reason and draft.dispute_reason.strip()):
        raise ValueError("Analyst set disputes_ranking=True without a dispute_reason.")

    if draft.forbidden_claims_used:
        raise ValueError(
            f"Analyst self-reported forbidden claims: {draft.forbidden_claims_used}"
        )

    blob = analyst_draft_text(draft)
    forbidden = contains_forbidden_claim(blob)
    if forbidden:
        raise ValueError(f"Analyst used forbidden claim(s): {forbidden}")
    leaks = contains_raw_score_leakage(blob)
    if leaks:
        raise ValueError(f"Analyst leaked internal scoring vocabulary: {leaks}")


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def build_analyst_draft(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
    model: BaseChatModel | None = None,
    revision_feedback: str | None = None,
) -> AnalystDraft:
    """Explain a DeterministicDecision. Raises ValueError on contract violations."""
    chat_model = _require_model(model)
    prompt = build_analyst_prompt(context, decision, revision_feedback=revision_feedback)
    draft = _generate_draft(chat_model, prompt)
    _validate_analyst_draft_against_decision(draft, decision, context)
    return draft


async def build_analyst_draft_async(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
    model: BaseChatModel | None = None,
    revision_feedback: str | None = None,
) -> AnalystDraft:
    chat_model = _require_model(model)
    prompt = build_analyst_prompt(context, decision, revision_feedback=revision_feedback)
    draft = await _generate_draft_async(chat_model, prompt)
    _validate_analyst_draft_against_decision(draft, decision, context)
    return draft
