from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.config import settings
from app.core.logging import get_logger
from app.repositories.agent_run_repository import AgentRunRepository, SqlAlchemyAgentRunRepository
from app.schemas.agent_trace import AgentRunRecord, AgentRunStatus


logger = get_logger(__name__)


class AgentRunRecorder:
    def __init__(self, repository: AgentRunRepository | None = None) -> None:
        self._repository = repository or SqlAlchemyAgentRunRepository()

    def set_repository(self, repository: AgentRunRepository) -> None:
        self._repository = repository

    def record_success(
        self,
        *,
        case_id: str,
        conversation_id: str | None,
        trace_id: str | None,
        layer: int,
        agent_name: str,
        run_order: int,
        input_summary: dict[str, Any] | None = None,
        output: Any = None,
        safety_report: Any = None,
        prompt: str | None = None,
        response_text: str | None = None,
        model: object | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        started_at: datetime | None = None,
    ) -> AgentRunRecord | None:
        ended_at = _now()
        record = self._build_record(
            case_id=case_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            layer=layer,
            agent_name=agent_name,
            run_order=run_order,
            status=AgentRunStatus.success,
            input_summary=input_summary,
            output=output,
            safety_report=safety_report,
            prompt=prompt,
            response_text=response_text,
            model=model,
            provider=provider,
            model_name=model_name,
            error_message=None,
            started_at=started_at or ended_at,
            ended_at=ended_at,
        )
        self._persist(record)
        return record

    def record_error(
        self,
        *,
        case_id: str,
        conversation_id: str | None,
        trace_id: str | None,
        layer: int,
        agent_name: str,
        run_order: int,
        error: BaseException | str,
        input_summary: dict[str, Any] | None = None,
        prompt: str | None = None,
        model: object | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        started_at: datetime | None = None,
    ) -> AgentRunRecord | None:
        ended_at = _now()
        message = str(error)
        if isinstance(error, BaseException):
            message = f"{error.__class__.__name__}: {error}"
        record = self._build_record(
            case_id=case_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            layer=layer,
            agent_name=agent_name,
            run_order=run_order,
            status=AgentRunStatus.error,
            input_summary=input_summary,
            output=None,
            safety_report=None,
            prompt=prompt,
            response_text=None,
            model=model,
            provider=provider,
            model_name=model_name,
            error_message=message,
            started_at=started_at or ended_at,
            ended_at=ended_at,
        )
        self._persist(record)
        return record

    def record_skipped(
        self,
        *,
        case_id: str,
        conversation_id: str | None,
        trace_id: str | None,
        layer: int,
        agent_name: str,
        run_order: int,
        input_summary: dict[str, Any] | None = None,
        output: Any = None,
        safety_report: Any = None,
        provider: str | None = None,
        model_name: str | None = None,
        started_at: datetime | None = None,
    ) -> AgentRunRecord | None:
        ended_at = _now()
        record = self._build_record(
            case_id=case_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            layer=layer,
            agent_name=agent_name,
            run_order=run_order,
            status=AgentRunStatus.skipped,
            input_summary=input_summary,
            output=output,
            safety_report=safety_report,
            prompt=None,
            response_text=None,
            model=None,
            provider=provider,
            model_name=model_name,
            error_message=None,
            started_at=started_at or ended_at,
            ended_at=ended_at,
        )
        self._persist(record)
        return record

    def _build_record(
        self,
        *,
        case_id: str,
        conversation_id: str | None,
        trace_id: str | None,
        layer: int,
        agent_name: str,
        run_order: int,
        status: AgentRunStatus,
        input_summary: dict[str, Any] | None,
        output: Any,
        safety_report: Any,
        prompt: str | None,
        response_text: str | None,
        model: object | None,
        provider: str | None,
        model_name: str | None,
        error_message: str | None,
        started_at: datetime,
        ended_at: datetime,
    ) -> AgentRunRecord:
        record_id = str(uuid4())
        output_json = _jsonable(output)
        safety_json = _jsonable(safety_report)
        response_payload = response_text if response_text is not None else _stable_json(output_json)
        prompt_chars = len(prompt or "")
        response_chars = len(response_payload or "")
        inferred_provider, inferred_model = _model_identity(model)
        prompt_artifact_ref = None
        response_artifact_ref = None
        if settings.cortex_trace_full_prompts:
            prompt_artifact_ref = _write_artifact(record_id, "prompt", prompt)
            response_artifact_ref = _write_artifact(record_id, "response", response_payload)
        return AgentRunRecord(
            id=record_id,
            case_id=case_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            layer=layer,
            agent_name=agent_name,
            run_order=run_order,
            status=status,
            model_name=model_name or inferred_model,
            provider=provider or inferred_provider,
            prompt_chars=prompt_chars,
            prompt_rough_tokens=_rough_tokens(prompt_chars),
            response_chars=response_chars,
            response_rough_tokens=_rough_tokens(response_chars),
            input_summary=input_summary or {},
            output_json=output_json,
            safety_report=safety_json if isinstance(safety_json, dict) else None,
            error_message=error_message,
            prompt_artifact_ref=prompt_artifact_ref,
            response_artifact_ref=response_artifact_ref,
            started_at=started_at,
            ended_at=ended_at,
        )

    def _persist(self, record: AgentRunRecord) -> None:
        try:
            _run_async(self._repository.add(record))
        except Exception as exc:
            logger.warning(
                "agent_run_trace.persist_failed case_id=%s trace_id=%s agent_name=%s error=%s: %s",
                record.case_id,
                record.trace_id,
                record.agent_name,
                exc.__class__.__name__,
                exc,
            )


agent_run_recorder = AgentRunRecorder()


def _run_async(awaitable) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(awaitable)
        return

    def _target() -> None:
        asyncio.run(awaitable)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()


def _now() -> datetime:
    return datetime.now(UTC)


def _rough_tokens(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, (chars + 3) // 4)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return {"repr": repr(value)}


def _stable_json(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _write_artifact(record_id: str, kind: str, payload: str | None) -> str | None:
    if payload is None:
        return None
    root = Path(settings.cortex_trace_artifact_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{record_id}.{kind}.txt"
        path.write_text(payload, encoding="utf-8")
        return f"local-debug-artifact://agent-runs/{path.name}"
    except OSError as exc:
        logger.warning(
            "agent_run_trace.artifact_write_failed record_id=%s kind=%s error=%s: %s",
            record_id,
            kind,
            exc.__class__.__name__,
            exc,
        )
        return None


def _model_identity(model: object | None) -> tuple[str | None, str | None]:
    if model is None:
        return None, None
    model_name = None
    for attr in ("model_name", "model", "model_id"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            model_name = value
            break
    cls = model.__class__
    module = cls.__module__.lower()
    if "openai" in module:
        provider = "openai"
    elif "google" in module or "genai" in module:
        provider = "google"
    elif "ollama" in module:
        provider = "ollama"
    else:
        provider = cls.__name__
    return provider, model_name or cls.__name__
