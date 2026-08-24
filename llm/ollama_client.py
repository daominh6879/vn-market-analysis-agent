from __future__ import annotations

import json
import time
from typing import Iterator

import httpx

from llm.base import LLMClient
from llm.exceptions import ModelOverloadedError, SourceError
from llm.types import LLMResponse, Message, ToolCall

_DEFAULT_MODEL = "llama3"
_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaClient(LLMClient):
    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        default_model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @property
    def is_cloud(self) -> bool:
        return bool(self._headers)

    def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(messages, model, max_tokens, system, tools, stream=False)
        t0 = time.perf_counter()
        try:
            resp = httpx.post(
                f"{self._base_url}/api/chat",
                json=payload,
                headers=self._headers,
                timeout=120,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise ModelOverloadedError(str(e), provider="ollama") from e
            raise SourceError(str(e), provider="ollama") from e
        except httpx.RequestError as e:
            raise SourceError(str(e), provider="ollama") from e
        elapsed = time.perf_counter() - t0

        data = resp.json()
        msg = data.get("message", {})
        text = msg.get("content", "")

        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", {})
            tool_calls.append(
                ToolCall(
                    id=str(i),
                    name=fn.get("name", ""),
                    input=raw_args if isinstance(raw_args, dict) else json.loads(raw_args),
                )
            )

        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=usage.get("prompt_tokens", data.get("prompt_eval_count", 0)),
            output_tokens=usage.get("completion_tokens", data.get("eval_count", 0)),
            model=data.get("model", model or self._default_model),
            stop_reason=data.get("done_reason", "stop"),
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
        payload = self._build_payload(messages, model, max_tokens, system, tools=None, stream=True)
        try:
            with httpx.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json=payload,
                headers=self._headers,
                timeout=120,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise ModelOverloadedError(str(e), provider="ollama") from e
            raise SourceError(str(e), provider="ollama") from e
        except httpx.RequestError as e:
            raise SourceError(str(e), provider="ollama") from e

    def _build_payload(
        self,
        messages: list[Message],
        model: str | None,
        max_tokens: int,
        system: str | None,
        tools: list[dict] | None,
        stream: bool,
    ) -> dict:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend({"role": m.role, "content": m.content} for m in messages)

        payload: dict = {
            "model": model or self._default_model,
            "messages": msgs,
            "stream": stream,
            "options": {"num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = tools
        return payload
