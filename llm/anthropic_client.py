from __future__ import annotations

import time
from typing import Iterator

import anthropic

from llm.base import LLMClient
from llm.exceptions import ContentBlockedError, ModelOverloadedError, ProviderError, SourceError
from llm.types import LLMResponse, Message, ToolCall

_DEFAULT_MODEL = "claude-opus-5"


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str | None = None, default_model: str = _DEFAULT_MODEL):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._default_model = default_model

    def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(messages, model, max_tokens, system, tools)
        t0 = time.perf_counter()
        try:
            resp = self._client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            raise ModelOverloadedError(str(e), provider="anthropic") from e
        except anthropic.BadRequestError as e:
            if "content" in str(e).lower():
                raise ContentBlockedError(str(e), provider="anthropic") from e
            raise ProviderError(str(e), provider="anthropic") from e
        except anthropic.APIStatusError as e:
            raise SourceError(str(e), provider="anthropic") from e
        elapsed = time.perf_counter() - t0

        text = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=resp.model,
            stop_reason=resp.stop_reason or "",
            elapsed_seconds=elapsed,
        )

    def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> Iterator[str]:
        kwargs = self._build_kwargs(messages, model, max_tokens, system, tools=None)
        try:
            with self._client.messages.stream(**kwargs) as s:
                yield from s.text_stream
        except anthropic.RateLimitError as e:
            raise ModelOverloadedError(str(e), provider="anthropic") from e
        except anthropic.BadRequestError as e:
            raise ContentBlockedError(str(e), provider="anthropic") from e
        except anthropic.APIStatusError as e:
            raise SourceError(str(e), provider="anthropic") from e

    def _build_kwargs(
        self,
        messages: list[Message],
        model: str | None,
        max_tokens: int,
        system: str | None,
        tools: list[dict] | None,
    ) -> dict:
        kwargs: dict = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        return kwargs
