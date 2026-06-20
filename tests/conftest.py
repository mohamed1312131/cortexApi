from __future__ import annotations

import pytest

from app.repositories.agent_run_repository import InMemoryAgentRunRepository
from app.services.tracing import agent_run_recorder


@pytest.fixture
def agent_run_repo() -> InMemoryAgentRunRepository:
    repo = InMemoryAgentRunRepository()
    agent_run_recorder.set_repository(repo)
    return repo


@pytest.fixture(autouse=True)
def _use_in_memory_agent_run_repo(agent_run_repo: InMemoryAgentRunRepository) -> None:
    return None
