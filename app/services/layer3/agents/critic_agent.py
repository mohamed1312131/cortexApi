# app/services/layer3/agents/critic_agent.py
from __future__ import annotations

import json

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import ValidationError

from app.core.logging import get_logger
from app.core.llm import get_chat_model
from app.schemas.layer3 import (
    AnalystDraft,
    CriticReview,
    CriticVerdict,
    DeterministicDecision,
    ReasoningContext,
)
from app.services.layer3.llm_response import extract_model_text, strip_code_fences
from app.services.layer3.prompt_compaction import (
    compact_allowed_evidence_refs,
    compact_deterministic_decision_for_prompt,
    compact_reasoning_context_for_prompt,
)
from app.services.layer3.safety_rules import (
    contains_forbidden_claim,
    contains_raw_score_leakage,
)

# Cortex Layer 3 Critic Agent.
#
# ADVISORY ONLY. The Critic reviews whether the AnalystDraft is clear,
# evidence-grounded, non-misleading, and aligned with the DeterministicDecision.
# It is NOT a second Analyst: it does not re-rank, change readiness bands, or write
# customer output. The deterministic Safety Gate runs AFTER the Critic and remains
# authoritative — the Critic verdict never overrides it.
#
# This module never imports or uses InternalScoringTrace or raw score values.
# Forbidden-claim / raw-score helpers are shared with the Analyst and Safety Gate
# via safety_rules so the three never drift.

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
CRITIC_PROMPT = """You are the Cortex Layer 3 Critic Agent.

<role>
Your job is to validate the AnalystDraft. You are a reviewer, not an author.
The DeterministicDecision below is FIXED TRUTH. You cannot change ranks, modes,
readiness bands, ranking types, or path families. You cannot invent facts. You
judge only against the ReasoningContext, the DeterministicDecision, and the
AnalystDraft provided.
</role>

<what_to_check>
- unsupported claims (statements not grounded in the evidence)
- weak, vague, or misleading wording
- hidden uncertainty, hidden hard gates, hidden unknowns, hidden conflicts
- missing or weak use of the provided evidence_refs
- contradiction with the deterministic ranking or readiness bands
- overconfident wording or unsafe implications (even without a forbidden phrase)
- vague or non-actionable next actions
- forbidden claims and raw-score leakage (per the shared safety policy)
</what_to_check>

<verdict_rules>
- verdict = "pass" when the AnalystDraft is semantically safe, clear, and grounded.
- verdict = "revise" for fixable clarity/evidence/wording issues. Provide concrete
  findings AND required_changes.
- verdict = "block" for serious unsupported or unsafe claims. Provide findings.
- If you believe the narrative contradicts the deterministic ranking, set
  contradiction_with_deterministic_ranking=true and do NOT use verdict "pass".
</verdict_rules>

<hard_rules>
- Return ONLY one CriticReview JSON object. No markdown, no commentary.
- Do not output a final customer-facing response. This is an internal review.
- Do not use or mention raw internal scores, raw_score, percentages, or internal
  scoring values.
- Never approve, clear, confirm, or guarantee anything.
</hard_rules>

<critic_review_shape>
{
  "verdict": "pass | revise | block",
  "findings": [
    {"code": "<short>", "severity": "low|medium|high|blocking",
     "message": "<what is wrong>", "evidence_refs": [], "required_change": "<or null>"}
  ],
  "unsupported_claims": [],
  "hidden_hard_gates": [],
  "hidden_unknowns": [],
  "contradiction_with_deterministic_ranking": false,
  "required_changes": []
}
</critic_review_shape>

<allowed_evidence_refs>
__ALLOWED_REFS__
</allowed_evidence_refs>

<reasoning_context>
__CONTEXT_JSON__
</reasoning_context>

<deterministic_decision>
__DECISION_JSON__
</deterministic_decision>

<analyst_draft>
__DRAFT_JSON__
</analyst_draft>

Return only the CriticReview JSON object.
"""


def build_critic_prompt(
    context: ReasoningContext,
    decision: DeterministicDecision,
    analyst_draft: AnalystDraft,
) -> str:
    allowed = compact_allowed_evidence_refs(context, decision)
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
    draft_json = json.dumps(
        analyst_draft.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    return (
        CRITIC_PROMPT.replace("__ALLOWED_REFS__", json.dumps(allowed, ensure_ascii=False))
        .replace("__CONTEXT_JSON__", context_json)
        .replace("__DECISION_JSON__", decision_json)
        .replace("__DRAFT_JSON__", draft_json)
    )


# --------------------------------------------------------------------------- #
# model plumbing (mirrors analyst_agent.py; injectable for tests)
# --------------------------------------------------------------------------- #
def _require_model(model: BaseChatModel | None) -> BaseChatModel:
    model = model or get_chat_model(layer3=True)
    if model is None:
        raise RuntimeError("No LLM configured (LLM_PROVIDER=none). Layer 3 Critic requires an LLM.")
    return model


def _validation_summary(exc: ValidationError) -> str:
    return json.dumps(exc.errors(include_input=False), ensure_ascii=False)


def _to_structured_review(result: object) -> CriticReview:
    if isinstance(result, CriticReview):
        return result
    if result is None:
        raise TypeError("none")
    if not isinstance(result, dict):
        raise TypeError(type(result).__name__)
    try:
        return CriticReview.model_validate(result)
    except ValidationError as exc:
        raise ValueError(
            f"Critic structured output did not match CriticReview: {_validation_summary(exc)}"
        ) from exc


def _parse_json_review(text: str) -> CriticReview:
    cleaned = strip_code_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Critic returned non-JSON output.") from exc
    try:
        return CriticReview.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"Critic JSON did not match CriticReview: {_validation_summary(exc)}"
        ) from exc


def _fallback_reason(result: object) -> str | None:
    if result is None:
        logger.info("critic.structured_output_none")
        return "none"
    if not isinstance(result, (CriticReview, dict)):
        logger.info("critic.structured_output_invalid_type type=%s", type(result).__name__)
        return "invalid_type"
    return None


def _parse_json_fallback(model: BaseChatModel, prompt: str, *, reason: str | None) -> CriticReview:
    if reason is not None:
        logger.info("critic.json_fallback_attempted reason=%s", reason)
    try:
        raw = model.invoke(prompt)
    except Exception as exc:
        if reason is not None:
            logger.info("critic.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise RuntimeError(
            f"Critic model call failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    try:
        return _parse_json_review(extract_model_text(raw))
    except ValueError as exc:
        if reason is not None:
            logger.info("critic.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise


async def _parse_json_fallback_async(
    model: BaseChatModel,
    prompt: str,
    *,
    reason: str | None,
) -> CriticReview:
    if reason is not None:
        logger.info("critic.json_fallback_attempted reason=%s", reason)
    try:
        raw = await model.ainvoke(prompt)
    except Exception as exc:
        if reason is not None:
            logger.info("critic.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise RuntimeError(
            f"Critic model call failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    try:
        return _parse_json_review(extract_model_text(raw))
    except ValueError as exc:
        if reason is not None:
            logger.info("critic.json_fallback_failed error_type=%s", exc.__class__.__name__)
        raise


def _generate_review(model: BaseChatModel, prompt: str) -> CriticReview:
    fallback_reason: str | None = None
    if hasattr(model, "with_structured_output"):
        try:
            structured = model.with_structured_output(CriticReview)
            result = structured.invoke(prompt)
            fallback_reason = _fallback_reason(result)
            if fallback_reason is None:
                return _to_structured_review(result)
        except NotImplementedError:
            fallback_reason = "not_implemented"
        except ValidationError as exc:
            raise ValueError(f"Critic structured output invalid: {exc}") from exc
        except ValueError:
            raise
        except TypeError as exc:
            fallback_reason = str(exc) or "invalid_type"
        except Exception as exc:
            raise RuntimeError(
                f"Critic model call failed: {exc.__class__.__name__}: {exc}"
            ) from exc

    return _parse_json_fallback(model, prompt, reason=fallback_reason)


async def _generate_review_async(model: BaseChatModel, prompt: str) -> CriticReview:
    fallback_reason: str | None = None
    if hasattr(model, "with_structured_output"):
        try:
            structured = model.with_structured_output(CriticReview)
            result = await structured.ainvoke(prompt)
            fallback_reason = _fallback_reason(result)
            if fallback_reason is None:
                return _to_structured_review(result)
        except NotImplementedError:
            fallback_reason = "not_implemented"
        except ValidationError as exc:
            raise ValueError(f"Critic structured output invalid: {exc}") from exc
        except ValueError:
            raise
        except TypeError as exc:
            fallback_reason = str(exc) or "invalid_type"
        except Exception as exc:
            raise RuntimeError(
                f"Critic model call failed: {exc.__class__.__name__}: {exc}"
            ) from exc

    return await _parse_json_fallback_async(model, prompt, reason=fallback_reason)


# --------------------------------------------------------------------------- #
# contract validation (advisory output discipline — NOT the safety gate)
# --------------------------------------------------------------------------- #
def _validate_critic_review(review: CriticReview) -> None:
    """Deterministic contract check. Raises ValueError on violation. Never repairs."""
    if review.verdict in (CriticVerdict.revise, CriticVerdict.block) and not review.findings:
        raise ValueError("Critic verdict revise/block requires non-empty findings.")

    if review.contradiction_with_deterministic_ranking and review.verdict is CriticVerdict.pass_:
        raise ValueError(
            "Critic cannot pass when contradiction_with_deterministic_ranking is True."
        )

    if review.verdict is CriticVerdict.pass_ and review.unsupported_claims:
        raise ValueError("Critic cannot pass with non-empty unsupported_claims.")

    if review.verdict is CriticVerdict.pass_:
        for claim in review.unsupported_claims:
            if contains_forbidden_claim(claim):
                raise ValueError("Critic cannot pass with forbidden claims in unsupported_claims.")

    if review.verdict is CriticVerdict.revise and not review.required_changes:
        raise ValueError("Critic verdict revise requires non-empty required_changes.")

    # raw-score leakage in finding messages / required_changes (Critic may QUOTE a
    # forbidden claim it found, so forbidden phrases are NOT scanned in findings).
    texts: list[str] = list(review.required_changes)
    for finding in review.findings:
        texts.append(finding.message)
        if finding.required_change:
            texts.append(finding.required_change)
    leaks = contains_raw_score_leakage(" ".join(texts))
    if leaks:
        raise ValueError(f"Critic leaked internal scoring vocabulary: {leaks}")


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def build_critic_review(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
    analyst_draft: AnalystDraft,
    model: BaseChatModel | None = None,
) -> CriticReview:
    """Advisory review of an AnalystDraft. Raises ValueError on contract violations."""
    chat_model = _require_model(model)
    prompt = build_critic_prompt(context, decision, analyst_draft)
    review = _generate_review(chat_model, prompt)
    _validate_critic_review(review)
    return review


async def build_critic_review_async(
    *,
    context: ReasoningContext,
    decision: DeterministicDecision,
    analyst_draft: AnalystDraft,
    model: BaseChatModel | None = None,
) -> CriticReview:
    chat_model = _require_model(model)
    prompt = build_critic_prompt(context, decision, analyst_draft)
    review = await _generate_review_async(chat_model, prompt)
    _validate_critic_review(review)
    return review
