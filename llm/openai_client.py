from __future__ import annotations

import time
from typing import Iterator

import openai

from llm.base import LLMClient
from llm.exceptions import ContentBlockedError, ModelOverloadedError, SourceError
from llm.types import LLMResponse, Message, ToolCall

_DEFAULT_MODEL = "gpt-4o"


class OpenAIClient(LLMClient):
    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = _DEFAULT_MODEL,
        base_url: str | None = None,
        strip_thinking_output: bool = False,
    ):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self._default_model = default_model
        self._strip_thinking = strip_thinking_output

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
        msgs = self._build_messages(messages, system)
        kwargs: dict = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            def _to_openai(t: dict) -> dict:
                fn = {k: v for k, v in t.items() if k not in ("input_schema", "strict")}
                if "input_schema" in t:
                    fn["parameters"] = t["input_schema"]
                return {"type": "function", "function": fn}
            kwargs["tools"] = [_to_openai(t) for t in tools]

        t0 = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.RateLimitError as e:
            raise ModelOverloadedError(str(e), provider="openai") from e
        except openai.BadRequestError as e:
            raise ContentBlockedError(str(e), provider="openai") from e
        except openai.APIStatusError as e:
            raise SourceError(str(e), provider="openai") from e
        elapsed = time.perf_counter() - t0

        choice = resp.choices[0]
        msg = choice.message
        text = msg.content or ""
        # DeepSeek reasoning models: content may be empty; answer lives in reasoning_content.
        # Only fall back to reasoning_content if content is truly absent (not just after stripping).
        if not text:
            text = getattr(msg, "reasoning_content", None) or ""

        import re as _re
        # Some models (deepseek-v4-flash) put the structured answer INSIDE <think> with nothing
        # after </think>. Detect this: strip think blocks; if result is empty, keep think content.
        think_inner = ""
        think_match = _re.search(r"<think>(.*?)</think>", text, flags=_re.DOTALL)
        if think_match:
            think_inner = think_match.group(1).strip()
        text_after_think = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
        if not text_after_think and think_inner:
            text = think_inner  # answer was inside <think>
        else:
            text = text_after_think

        if self._strip_thinking:
            from llm.utils import strip_thinking as _strip
            text = _strip(text)

        tool_calls: list[ToolCall] = []
        import json
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Model returned truncated/malformed JSON (often from low max_tokens).
                # Attempt partial repair: find last complete key-value by truncating to last '}'.
                import re as _re2
                repaired = raw[: raw.rfind("}") + 1] if "}" in raw else "{}"
                try:
                    parsed = json.loads(repaired)
                except json.JSONDecodeError:
                    parsed = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=parsed))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            model=resp.model,
            stop_reason=choice.finish_reason or "",
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
        msgs = self._build_messages(messages, system)
        try:
            with self._client.chat.completions.stream(
                model=model or self._default_model,
                max_tokens=max_tokens,
                messages=msgs,
            ) as s:
                for event in s:
                    if hasattr(event, "type") and event.type == "content.delta":
                        yield event.delta
        except openai.RateLimitError as e:
            raise ModelOverloadedError(str(e), provider="openai") from e
        except openai.BadRequestError as e:
            raise ContentBlockedError(str(e), provider="openai") from e
        except openai.APIStatusError as e:
            raise SourceError(str(e), provider="openai") from e

    @staticmethod
    def _build_messages(messages: list[Message], system: str | None) -> list[dict]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend({"role": m.role, "content": m.content} for m in messages)
        return msgs
