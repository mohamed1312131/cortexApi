from __future__ import annotations

from app.services.layer4.prompt import build_layer4_prompt
from app.services.prompt_budget import assert_prompt_under_budget
from tests.prompt_budget_helpers import build_prompt_budget_case


def test_layer4_prompt_under_budget_for_multimode_case():
    case = build_prompt_budget_case()
    prompt = build_layer4_prompt(case["layer4_request"])

    assert_prompt_under_budget("layer4_report", prompt, max_tokens=8000)


def test_layer4_prompt_excludes_full_block_summaries():
    case = build_prompt_budget_case()
    prompt = build_layer4_prompt(case["layer4_request"])

    assert '"block_summaries"' not in prompt
    assert '"block_responses"' not in prompt
    assert '"data_excerpt"' not in prompt


def test_layer4_prompt_excludes_analyst_draft_and_critic_review():
    case = build_prompt_budget_case()
    prompt = build_layer4_prompt(case["layer4_request"])

    assert '"analyst_draft"' not in prompt
    assert '"critic_review"' not in prompt


def test_layer4_prompt_uses_operational_evidence_and_minimal_layer2_support():
    case = build_prompt_budget_case()
    prompt = build_layer4_prompt(case["layer4_request"])

    assert '"operational_evidence"' in prompt
    assert '"layer2_support"' in prompt
    assert '"layer2_summary"' not in prompt
    assert '"completeness_status"' in prompt
    assert '"modes_covered"' in prompt
