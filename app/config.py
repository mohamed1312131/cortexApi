from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    api_port: int = 8000
    database_url: str = "postgresql+asyncpg://cortex:cortex@postgres:5432/cortex"
    redis_url: str = "redis://redis:6379/0"
    llm_provider: str = "none"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    google_ai_api_key: str = ""
    google_ai_model: str = "gemma-4-26b-a4b-it"
    google_ai_intake_model: str = ""
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
