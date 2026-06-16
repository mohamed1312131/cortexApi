from __future__ import annotations

import pytest

from app.services.layer3.llm_response import extract_model_text, extract_text_content


class _Msg:
    def __init__(self, content):
        self.content = content


class _Part:
    def __init__(self, text: str, *, thought: bool = False) -> None:
        self.text = text
        self.thought = thought


def test_extracts_final_json_after_thought_part():
    content = [
        {"text": "internal chain of thought", "thought": True},
        {"text": '{"verdict":"pass"}'},
    ]

    assert extract_model_text(_Msg(content)) == '{"verdict":"pass"}'


def test_thought_only_response_fails_safely():
    thought = '{"raw_score": 99, "private": true}'

    with pytest.raises(ValueError) as excinfo:
        extract_text_content([{"text": thought, "thought": True}])

    message = str(excinfo.value)
    assert "non-thought text" in message
    assert thought not in message
    assert "raw_score" not in message


def test_normal_string_response_still_works():
    assert extract_model_text(_Msg('{"ok": true}')) == '{"ok": true}'


def test_non_thought_parts_join_in_order():
    content = [
        {"text": '{"a":'},
        _Part("1"),
        "}",
    ]

    assert extract_text_content(content) == '{"a":\n1\n}'


def test_object_like_thought_part_is_ignored():
    content = [
        _Part("hidden thought", thought=True),
        _Part('{"final": true}'),
    ]

    assert extract_text_content(content) == '{"final": true}'
