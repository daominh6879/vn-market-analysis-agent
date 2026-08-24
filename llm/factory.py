from __future__ import annotations

import os

from llm.base import LLMClient


def create_search_tool(max_results: int = 5):
    """Return a TavilySearchResults tool. Requires TAVILY_API_KEY in env."""
    from langchain_community.tools.tavily_search import TavilySearchResults
    return TavilySearchResults(
        max_results=max_results,
        tavily_api_key=os.environ.get("TAVILY_API_KEY"),
    )


def _wrap_langfuse(client: LLMClient) -> LLMClient:
    """Wrap client with Langfuse tracing if keys are present in env."""
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return client
    from llm.langfuse_wrapper import LangfuseClientWrapper
    return LangfuseClientWrapper(client)


def create_client() -> LLMClient:
    provider = os.environ.get("LLM_PROVIDER", "deepseek").lower()

    if provider == "anthropic":
        from llm.anthropic_client import AnthropicClient
        client = AnthropicClient(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            default_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
        )

    elif provider == "ollama":
        from llm.ollama_client import OllamaClient
        force_local = os.environ.get("OLLAMA_LOCAL", "").lower() in ("1", "true", "yes")
        api_key = None if force_local else os.environ.get("OLLAMA_API_KEY")
        base_url = (
            os.environ.get("OLLAMA_CLOUD_HOST", "https://ollama.com")
            if api_key
            else os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        )
        model = (
            os.environ.get("OLLAMA_CLOUD_MODEL", "gpt-oss:20b")
            if api_key
            else os.environ.get("OLLAMA_MODEL", "llama3")
        )
        client = OllamaClient(
            base_url=base_url,
            default_model=model,
            api_key=api_key,
        )

    elif provider == "openai":
        from llm.openai_client import OpenAIClient
        client = OpenAIClient(
            api_key=os.environ.get("OPENAI_API_KEY"),
            default_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        )

    elif provider == "deepseek":
        from llm.openai_client import OpenAIClient
        client = OpenAIClient(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            default_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url="https://api.deepseek.com",
        )

    elif provider == "gemini":
        from llm.gemini_client import GeminiClient
        client = GeminiClient(
            api_key=os.environ.get("GEMINI_API_KEY"),
            default_model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        )

    else:
        raise ValueError(f"Unknown LLM_PROVIDER={provider!r}. Choose 'anthropic', 'ollama', 'openai', 'gemini', or 'deepseek'.")

    return _wrap_langfuse(client)
