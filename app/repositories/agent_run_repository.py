from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, insert, select
from sqlalchemy.types import JSON

from app.core.db import AsyncSessionLocal
from app.schemas.agent_trace import AgentRunRecord


metadata = MetaData()

agent_runs_table = Table(
    "agent_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("case_id", String(255), nullable=False, index=True),
    Column("conversation_id", String(255), nullable=True, index=True),
    Column("trace_id", String(255), nullable=True, index=True),
    Column("layer", Integer, nullable=False),
    Column("agent_name", String(120), nullable=False),
    Column("run_order", Integer, nullable=False),
    Column("status", String(40), nullable=False),
    Column("model_name", String(255), nullable=True),
    Column("provider", String(120), nullable=True),
    Column("prompt_chars", Integer, nullable=False, default=0),
    Column("prompt_rough_tokens", Integer, nullable=False, default=0),
    Column("response_chars", Integer, nullable=False, default=0),
    Column("response_rough_tokens", Integer, nullable=False, default=0),
    Column("input_summary", JSON, nullable=False, default=dict),
    Column("output_json", JSON, nullable=True),
    Column("safety_report", JSON, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("prompt_artifact_ref", Text, nullable=True),
    Column("response_artifact_ref", Text, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=False),
)


class AgentRunRepository(Protocol):
    async def add(self, record: AgentRunRecord) -> None:
        ...

    async def list_by_case(self, case_id: str) -> list[AgentRunRecord]:
        ...


class SqlAlchemyAgentRunRepository:
    async def add(self, record: AgentRunRecord) -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(insert(agent_runs_table).values(**_to_row(record)))
            await session.commit()

    async def list_by_case(self, case_id: str) -> list[AgentRunRecord]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(agent_runs_table)
                .where(agent_runs_table.c.case_id == case_id)
                .order_by(
                    agent_runs_table.c.started_at.asc(),
                    agent_runs_table.c.run_order.asc(),
                    agent_runs_table.c.agent_name.asc(),
                )
            )
            return [AgentRunRecord.model_validate(dict(row._mapping)) for row in result]


class InMemoryAgentRunRepository:
    """Small test/debug repository; production uses SqlAlchemyAgentRunRepository."""

    def __init__(self) -> None:
        self.records: list[AgentRunRecord] = []

    async def add(self, record: AgentRunRecord) -> None:
        self.records.append(record)

    async def list_by_case(self, case_id: str) -> list[AgentRunRecord]:
        return sorted(
            [record for record in self.records if record.case_id == case_id],
            key=lambda record: (record.started_at, record.run_order, record.agent_name),
        )


def _to_row(record: AgentRunRecord) -> dict[str, Any]:
    data = record.model_dump(mode="json")
    data["status"] = record.status.value
    data["started_at"] = _strip_timezone_marker(record.started_at)
    data["ended_at"] = _strip_timezone_marker(record.ended_at)
    return data


def _strip_timezone_marker(value: datetime) -> datetime:
    return value
