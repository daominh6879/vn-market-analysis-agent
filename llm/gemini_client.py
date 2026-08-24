from __future__ import annotations

import time
from typing import Iterator

from google import genai
from google.genai import types as gtypes
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

from llm.base import LLMClient
from llm.exceptions import ContentBlockedError, ModelOverloadedError, SourceError
from llm.types import LLMResponse, Message, ToolCall

_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiClient(LLMClient):
    def __init__(self, api_key: str | None = None, default_model: str = _DEFAULT_MODEL):
        self._client = genai.Client(api_key=api_key)
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
        contents, config = self._build_request(messages, max_tokens, system, tools)
        t0 = time.perf_counter()
        try:
            resp = self._client.models.generate_content(
                model=model or self._default_model,
                contents=contents,
                config=config,
            )
        except ResourceExhausted as e:
            raise ModelOverloadedError(str(e), provider="gemini") from e
        except ServiceUnavailable as e:
            raise SourceError(str(e), provider="gemini") from e
        except Exception as e:
            msg = str(e).lower()
            if "safety" in msg or "blocked" in msg:
                raise ContentBlockedError(str(e), provider="gemini") from e
            raise SourceError(str(e), provider="gemini") from e
        elapsed = time.perf_counter() - t0

        text = ""
        tool_calls: list[ToolCall] = []
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate:
            for part in (candidate.content.parts or []):
                if part.text:
                    text += part.text
                if part.function_call and part.function_call.name:
                    fc = part.function_call
                    tool_calls.append(
                        ToolCall(id=fc.name, name=fc.name, input=dict(fc.args or {}))
                    )

        meta = resp.usage_metadata
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            model=model or self._default_model,
            stop_reason=str(candidate.finish_reason) if candidate else "",
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
        contents, config = self._build_request(messages, max_tokens, system, tools=None)
        try:
            for chunk in self._client.models.generate_content_stream(
                model=model or self._default_model,
                contents=contents,
                config=config,
            ):
                if chunk.text:
                    yield chunk.text
        except ResourceExhausted as e:
            raise ModelOverloadedError(str(e), provider="gemini") from e
        except ServiceUnavailable as e:
            raise SourceError(str(e), provider="gemini") from e
        except Exception as e:
            msg = str(e).lower()
            if "safety" in msg or "blocked" in msg:
                raise ContentBlockedError(str(e), provider="gemini") from e
            raise SourceError(str(e), provider="gemini") from e

    def _build_request(
        self,
        messages: list[Message],
        max_tokens: int,
        system: str | None,
        tools: list[dict] | None,
    ) -> tuple[list, gtypes.GenerateContentConfig]:
        role_map = {"user": "user", "assistant": "model"}
        contents = [
            gtypes.Content(
                role=role_map.get(m.role, m.role),
                parts=[gtypes.Part(text=m.content)],
            )
            for m in messages
        ]
        config = gtypes.GenerateContentConfig(
            max_output_tokens=max_tokens,
            system_instruction=system,
            tools=tools,
        )
        return contents, config
