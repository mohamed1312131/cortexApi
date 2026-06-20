from __future__ import annotations

from app.services.layer3.agents.analyst_agent import build_analyst_prompt
from app.services.layer3.agents.critic_agent import build_critic_prompt
from app.services.layer3.prompt_compaction import compact_reasoning_context_for_prompt
from app.services.prompt_budget import assert_prompt_under_budget
from tests.prompt_budget_helpers import build_prompt_budget_case


def test_layer3_analyst_prompt_under_budget():
    case = build_prompt_budget_case()
    prompt = build_analyst_prompt(case["context"], case["deterministic"])

    assert_prompt_under_budget("layer3_analyst", prompt, max_tokens=7000)


def test_layer3_critic_prompt_under_budget():
    case = build_prompt_budget_case()
    prompt = build_critic_prompt(
        case["context"],
        case["deterministic"],
        case["analyst_draft"],
    )

    assert_prompt_under_budget("layer3_critic", prompt, max_tokens=7000)


def test_layer3_prompts_do_not_include_full_fact_package():
    case = build_prompt_budget_case()

    analyst_prompt = build_analyst_prompt(case["context"], case["deterministic"])
    critic_prompt = build_critic_prompt(
        case["context"],
        case["deterministic"],
        case["analyst_draft"],
    )

    for prompt in (analyst_prompt, critic_prompt):
        assert '"block_responses"' not in prompt
        assert '"fetch_plan"' not in prompt
        assert '"data_excerpt"' not in prompt


def test_layer3_unknowns_are_capped_or_grouped():
    case = build_prompt_budget_case()

    compact_context = compact_reasoning_context_for_prompt(
        case["context"],
        case["deterministic"],
    )

    assert len(compact_context["top_unknowns"]) <= 10
    fields = {item["code"] for item in compact_context["top_unknowns"]}
    assert "cargo_flags.pharma" not in fields
    assert "cargo_flags.food_perishable" not in fields
    assert "cargo_flags.live_animals" not in fields
