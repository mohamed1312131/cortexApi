from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from app.config import settings


def text_size(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "bytes": len(text.encode("utf-8")),
        # Cheap approximation for trend/debug logs. Provider tokenizers differ.
        "tokens_est": max(1, len(text) // 4) if text else 0,
    }


def payload_size(value: Any) -> dict[str, int]:
    try:
        if isinstance(value, BaseModel):
            payload = value.model_dump_json()
        else:
            payload = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        payload = repr(value)
    return text_size(payload)


def log_prompt_size(
    logger: logging.Logger,
    *,
    label: str,
    prompt: str,
    trace_id: str | None = None,
    case_id: str | None = None,
) -> None:
    size = text_size(prompt)
    level = logging.WARNING if size["chars"] > settings.prompt_size_warn_chars else logging.INFO
    logger.log(
        level,
        "prompt.size label=%s trace_id=%s case_id=%s chars=%d bytes=%d tokens_est=%d warn_chars=%d",
        label,
        trace_id,
        case_id,
        size["chars"],
        size["bytes"],
        size["tokens_est"],
        settings.prompt_size_warn_chars,
    )


def log_payload_size(
    logger: logging.Logger,
    *,
    label: str,
    value: Any,
    trace_id: str | None = None,
    case_id: str | None = None,
) -> None:
    size = payload_size(value)
    level = logging.WARNING if size["bytes"] > settings.payload_size_warn_bytes else logging.INFO
    logger.log(
        level,
        "payload.size label=%s trace_id=%s case_id=%s chars=%d bytes=%d tokens_est=%d warn_bytes=%d",
        label,
        trace_id,
        case_id,
        size["chars"],
        size["bytes"],
        size["tokens_est"],
        settings.payload_size_warn_bytes,
    )
