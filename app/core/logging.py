"""Lightweight structured logging for the Cortex API.

This is intentionally dependency-free (stdlib ``logging`` only). Log lines use a
``key=value`` convention so they are greppable and easy to ship to a structured
backend later. No secrets are logged; only identifiers, enum values, and counts.

LangSmith tracing is configured separately in ``app.core.tracing`` and is NOT
required for these logs.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_LOGGER_ROOT = "cortex"
_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the ``cortex`` logger namespace. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger(_LOGGER_ROOT).setLevel(level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``cortex`` namespace."""
    return logging.getLogger(f"{_LOGGER_ROOT}.{name}")


def log_layer1_outcome(
    logger: logging.Logger,
    *,
    trace_id: str,
    endpoint: str,
    result: Any,
) -> None:
    """Log a coarse Layer 1 outcome from an ``IntakeResult``.

    Derived purely from the boundary-visible result (route decision, readiness,
    blocking fields, changed fields). It does not reach into the Layer 1 graph
    nodes, so it cannot change routing or extraction behavior.
    """
    blocking: list[str] = []
    if getattr(result, "intake_json", None) is not None:
        blocking = list(result.intake_json.missing_fields.blocking)

    logger.info(
        "layer1.outcome trace_id=%s endpoint=%s case_id=%s case_action=%s "
        "intent=%s ready_for_layer_2=%s changed_fields=%d blocking_count=%d blocking=%s",
        trace_id,
        endpoint,
        result.case_id,
        result.case_action.value,
        result.intent.value,
        result.ready_for_layer_2,
        len(result.changed_fields),
        len(blocking),
        blocking,
    )
