from __future__ import annotations

from typing import Iterator

from langfuse import get_client, observe

from llm.base import LLMClient
from llm.types import LLMResponse, Message


class LangfuseClientWrapper(LLMClient):
    """Wraps any LLMClient and traces generate() calls via Langfuse v4 @observe."""

    def __init__(self, inner: LLMClient):
        self._inner = inner

    @observe(as_type="generation")
    def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        resp = self._inner.generate(
            messages, model=model, max_tokens=max_tokens, system=system, tools=tools, temperature=temperature
        )
        get_client().update_current_generation(
            name="llm.generate",
            input=[{"role": m.role, "content": m.content} for m in messages],
            output=resp.text,
            model=resp.model,
            usage_details={
                "input": resp.input_tokens,
                "output": resp.output_tokens,
            },
        )
        return resp

    def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> Iterator[str]:
        yield from self._inner.stream(
            messages, model=model, max_tokens=max_tokens, system=system
        )
