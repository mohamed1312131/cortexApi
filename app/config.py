from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> parents[1] is the repository root (``/app`` in Docker).
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# The settings file is found from the code location, never from the working
# directory: starting the process from another folder loads the same file.
# The file is optional. When it does not exist (the Docker image has none;
# compose passes the environment), settings come from the process environment
# and the defaults below, and the fail-closed defaults decide what happens:
# without APP_ENV and API keys the process refuses to start (app/core/auth.py).
ENV_FILE = PACKAGE_ROOT / ".env"

LOCAL_APP_ENV = "local"


class Settings(BaseSettings):
    # Not "local" by default: every local-only convenience (start without API
    # keys, CORTEX_AUTH_DISABLED, /docs) has to be asked for with an explicit
    # APP_ENV=local. An unset APP_ENV behaves like production.
    app_env: str = "production"
    # 8100 by default: the legacy Backend keeps 8000 (D15).
    api_port: int = 8100
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
    # Folder that contains the air/, sea/ and road/ data files. Empty = the
    # repository's own data/ folder, resolved from the package location (never
    # from the working directory). See app/core/data_paths.py.
    cortex_data_dir: str = ""
    # Temporary API-key auth (replaced by the tenants table in Wave 2).
    # JSON object {"<sha256 hex of the API key>": "<tenant_id>"}. Raw keys are
    # never stored. Empty outside APP_ENV=local = the process refuses to start.
    # Empty with APP_ENV=local = it starts and every /api/v1 request is refused.
    cortex_api_keys_json: str = ""
    # Switches API-key auth off. Only honoured together with an explicit
    # APP_ENV=local; with any other APP_ENV the process refuses to start.
    cortex_auth_disabled: bool = False
    orchestrator_cache_enabled: bool = True
    orchestrator_cache_ttl_seconds: int = 60 * 60 * 24 * 7
    full_response_include_artifacts: bool = False
    cortex_trace_full_prompts: bool = False
    cortex_trace_artifact_dir: str = "/tmp/cortex-agent-traces"
    llm_provider: str = "none"
    llm_intake_provider: str = ""
    llm_layer3_provider: str = ""
    llm_layer4_provider: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    google_ai_api_key: str = ""
    google_ai_model: str = "gemma-4-26b-a4b-it"
    google_ai_intake_model: str = ""
    google_ai_layer3_model: str = ""
    google_ai_layer4_model: str = ""
    intake_max_output_tokens: int = 2048
    layer3_max_output_tokens: int = 4096
    layer4_max_output_tokens: int = 4096
    prompt_size_warn_chars: int = 40_000
    payload_size_warn_bytes: int = 120_000
    # Gemini 2.5 thinking_budget:
    # -2 = do not send the parameter, -1 = dynamic, 0 = disabled where supported,
    # positive integer = budget cap.
    google_ai_thinking_budget: int = -2
    google_ai_intake_thinking_budget: int = -2
    google_ai_layer3_thinking_budget: int = -2
    google_ai_layer4_thinking_budget: int = -2
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_intake_model: str = ""
    ollama_layer3_model: str = ""
    ollama_layer4_model: str = ""
    ollama_temperature: float = 0.0
    # LangSmith sends prompts and outputs to a third party: off unless a
    # deployment turns it on explicitly (S4).
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "cortex-api"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_local(self) -> bool:
        """True only for APP_ENV=local (never by default)."""
        return self.app_env.strip().lower() == LOCAL_APP_ENV


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
        if not any(token in lower for token in ("model", "google", "gemini", "llm", "ollama")):
            continue
        try:
            value = getattr(selected, name)
        except Exception as exc:
            value = f"{type(exc).__name__}: {exc}"
        if "key" in lower and value:
            value = "***REDACTED***"
        values.append((name, value))
    return values
