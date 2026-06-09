from langchain_core.language_models.chat_models import BaseChatModel

from app.config import settings


def get_chat_model(*, intake: bool = False) -> BaseChatModel | None:
    """
    Returns a chat model based on LLM_PROVIDER.
    intake=True selects the lighter intake model when configured.
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

        model_name = (
            settings.google_ai_intake_model
            if intake and settings.google_ai_intake_model
            else settings.google_ai_model
        )

        return ChatGoogleGenerativeAI(
            model=model_name,
            api_key=settings.google_ai_api_key,
            max_tokens=settings.intake_max_output_tokens,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
