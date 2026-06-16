from __future__ import annotations


_THOUGHT_TYPES = {"thinking", "reasoning"}


def strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def extract_model_text(raw: object) -> str:
    text = getattr(raw, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    content = getattr(raw, "content", raw)
    return extract_text_content(content)


def extract_text_content(content: object) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            text = _extract_part_text(part)
            if text is not None:
                text_parts.append(text)
        combined = "\n".join(text_parts).strip()
        if combined:
            return combined
        raise ValueError("Model response did not include non-thought text content.")

    fallback = str(content).strip()
    if fallback:
        return fallback
    raise ValueError("Model response did not include text content.")


def _extract_part_text(part: object) -> str | None:
    if isinstance(part, str):
        return part

    if isinstance(part, dict):
        if part.get("thought") is True or part.get("type") in _THOUGHT_TYPES:
            return None
        text = part.get("text")
        return text if isinstance(text, str) else None

    if getattr(part, "thought", False) is True or getattr(part, "type", None) in _THOUGHT_TYPES:
        return None
    text = getattr(part, "text", None)
    return text if isinstance(text, str) else None
