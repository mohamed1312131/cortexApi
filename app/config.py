from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    api_port: int = 8000
    # Number of uvicorn worker processes (read by the Docker/runtime command).
    # 1 for local dev; raise (e.g. 4) for production-like multi-worker runtime.
    cortex_api_workers: int = 1
    # Size of the per-worker thread pool used by `asyncio.to_thread` (the blocking
    # Layer 1 path). 0 = keep Python's default (min(32, cpu+4)). Raise to allow
    # more concurrent blocking LLM calls per worker.
    cortex_api_thread_workers: int = 0
    # When True (dev), case-state degrades to an in-memory store if Redis fails.
    # When False (production), Redis failures fail loudly instead of silently
    # using per-process in-memory state that is unsafe across workers.
    cortex_redis_fallback_enabled: bool = True
    database_url: str = "postgresql+asyncpg://cortex:cortex@postgres:5432/cortex"
    redis_url: str = "redis://redis:6379/0"
    llm_provider: str = "none"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    google_ai_api_key: str = ""
    google_ai_model: str = "gemma-4-26b-a4b-it"
    google_ai_intake_model: str = ""
    google_ai_layer3_model: str = ""
    google_ai_layer4_model: str = ""
    intake_max_output_tokens: int = 2048
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "cortex-api"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def iter_model_config_debug_values(
    current_settings: Settings | None = None,
) -> list[tuple[str, object]]:
    """Return LLM/model-related settings with secret values redacted."""
    selected = current_settings or settings
    values: list[tuple[str, object]] = []
    for name in type(selected).model_fields:
        lower = name.lower()
        if not any(token in lower for token in ("model", "google", "gemini", "llm")):
            continue
        try:
            value = getattr(selected, name)
        except Exception as exc:
            value = f"{type(exc).__name__}: {exc}"
        if "key" in lower and value:
            value = "***REDACTED***"
        values.append((name, value))
    return values
