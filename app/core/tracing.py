import os

from langsmith import Client

from app.config import settings

_client: Client | None = None


def init_langsmith() -> Client | None:
    global _client

    if not settings.langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        _client = None
        return None

    if not settings.langsmith_api_key:
        raise RuntimeError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true.")

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project

    _client = Client(api_key=settings.langsmith_api_key)
    return _client

