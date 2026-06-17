from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings


def _configured(value: str) -> str:
    return value.strip()


def get_layer_provider(
    *,
    intake: bool = False,
    layer3: bool = False,
    layer4: bool = False,
) -> str:
    if intake and _configured(settings.llm_intake_provider):
        return _configured(settings.llm_intake_provider).lower()
    if layer3 and _configured(settings.llm_layer3_provider):
        return _configured(settings.llm_layer3_provider).lower()
    if layer4 and _configured(settings.llm_layer4_provider):
        return _configured(settings.llm_layer4_provider).lower()
    return _configured(settings.llm_provider).lower()


def get_google_model_name(
    *,
    intake: bool = False,
    layer3: bool = False,
    layer4: bool = False,
) -> str:
    if intake:
        return _configured(settings.google_ai_intake_model) or _configured(settings.google_ai_model)
    if layer3:
        return _configured(settings.google_ai_layer3_model) or _configured(settings.google_ai_model)
    if layer4:
        return (
            _configured(settings.google_ai_layer4_model)
            or _configured(settings.google_ai_layer3_model)
            or _configured(settings.google_ai_model)
        )
    return _configured(settings.google_ai_model)


def get_ollama_model_name(
    *,
    intake: bool = False,
    layer3: bool = False,
    layer4: bool = False,
) -> str:
    if intake:
        return _configured(settings.ollama_intake_model) or _configured(settings.ollama_model)
    if layer3:
        return _configured(settings.ollama_layer3_model) or _configured(settings.ollama_model)
    if layer4:
        return _configured(settings.ollama_layer4_model) or _configured(settings.ollama_model)
    return _configured(settings.ollama_model)


def get_layer_max_tokens(
    *,
    intake: bool = False,
    layer3: bool = False,
    layer4: bool = False,
) -> int | None:
    if layer3:
        value = settings.layer3_max_output_tokens
    elif layer4:
        value = settings.layer4_max_output_tokens
    else:
        value = settings.intake_max_output_tokens
    return value if value > 0 else None


def get_google_thinking_budget(
    *,
    intake: bool = False,
    layer3: bool = False,
    layer4: bool = False,
) -> int | None:
    if layer3 and settings.google_ai_layer3_thinking_budget >= -1:
        return settings.google_ai_layer3_thinking_budget
    if layer4 and settings.google_ai_layer4_thinking_budget >= -1:
        return settings.google_ai_layer4_thinking_budget
    if intake and settings.google_ai_intake_thinking_budget >= -1:
        return settings.google_ai_intake_thinking_budget
    if settings.google_ai_thinking_budget >= -1:
        return settings.google_ai_thinking_budget
    return None


def get_chat_model(
    *,
    intake: bool = False,
    layer3: bool = False,
    layer4: bool = False,
) -> BaseChatModel | None:
    """
    Returns a chat model based on LLM_PROVIDER.
    intake=True selects the lighter intake model when configured.
    layer3=True selects the Layer 3 model when configured.
    layer4=True selects the Layer 4 report model when configured.
    """
    provider = get_layer_provider(intake=intake, layer3=layer3, layer4=layer4)

    if provider in {"", "none", "disabled"}:
        return None

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER=openai.")

        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
        )

    if provider in {"google", "gemma", "gemini"}:
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.google_ai_api_key:
            raise RuntimeError("GOOGLE_AI_API_KEY is required when LLM_PROVIDER=google.")

        model_name = get_google_model_name(intake=intake, layer3=layer3, layer4=layer4)
        kwargs = {
            "model": model_name,
            "api_key": settings.google_ai_api_key,
            "max_tokens": get_layer_max_tokens(
                intake=intake,
                layer3=layer3,
                layer4=layer4,
            ),
            "timeout": 30,
        }
        thinking_budget = get_google_thinking_budget(
            intake=intake,
            layer3=layer3,
            layer4=layer4,
        )
        if thinking_budget is not None:
            kwargs["thinking_budget"] = thinking_budget

        try:
            return ChatGoogleGenerativeAI(**kwargs)
        except Exception as exc:
            if "thinking_budget" not in kwargs or "thinking_budget" not in str(exc):
                raise
            kwargs.pop("thinking_budget")
            return ChatGoogleGenerativeAI(**kwargs)

    if provider in {"ollama", "local"}:
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise RuntimeError(
                "langchain-ollama is required when using provider=ollama. "
                "Install dependencies or rebuild the api image."
            ) from exc

        model_name = get_ollama_model_name(intake=intake, layer3=layer3, layer4=layer4)
        if not model_name:
            raise RuntimeError("OLLAMA_MODEL is required when using provider=ollama.")

        kwargs = {
            "model": model_name,
            "base_url": settings.ollama_base_url,
            "temperature": settings.ollama_temperature,
            "timeout": 60,
        }
        max_tokens = get_layer_max_tokens(intake=intake, layer3=layer3, layer4=layer4)
        if max_tokens is not None:
            kwargs["num_predict"] = max_tokens
        return ChatOllama(**kwargs)

    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")
