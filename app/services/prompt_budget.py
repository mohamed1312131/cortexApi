from __future__ import annotations

import math


def char_count(text: str) -> int:
    return len(text)


def rough_token_count(text: str) -> int:
    return math.ceil(char_count(text) / 4)


def summarize_prompt_size(name: str, text: str) -> dict[str, int | str]:
    chars = char_count(text)
    return {
        "name": name,
        "chars": chars,
        "rough_tokens": math.ceil(chars / 4),
    }


def assert_prompt_under_budget(name: str, text: str, max_tokens: int) -> None:
    tokens = rough_token_count(text)
    if tokens > max_tokens:
        raise AssertionError(
            f"{name} prompt is over budget: {tokens} rough tokens > {max_tokens}; "
            f"chars={char_count(text)}"
        )
