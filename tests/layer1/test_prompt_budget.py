from __future__ import annotations

from app.services.layer1.intake_agent import _turn_payload
from app.services.prompt_budget import assert_prompt_under_budget
from tests.prompt_budget_helpers import MESSAGE


def test_layer1_intake_prompt_under_budget():
    prompt = _turn_payload(
        MESSAGE,
        previous_request_json=None,
        conversation_summary=None,
    )

    assert_prompt_under_budget("layer1_intake", prompt, max_tokens=4000)
