from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings


def _configured(value: str) -> str:
    return value.strip()


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
    provider = settings.llm_provider.strip().lower()

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

        return ChatGoogleGenerativeAI(
            model=model_name,
            api_key=settings.google_ai_api_key,
            max_tokens=settings.intake_max_output_tokens,
            timeout=30,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
