"""Unit tests for the LLM middleware — no network calls."""
from __future__ import annotations

import os
from typing import Iterator

import pytest

from llm.base import LLMClient
from llm.exceptions import ContentBlockedError, ModelOverloadedError, ProviderError
from llm.factory import create_client
from llm.types import LLMResponse, Message, ToolCall


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------

class FakeClient(LLMClient):
    def __init__(self, response_text: str = "pong"):
        self.calls: list[list[Message]] = []
        self._text = response_text

    def generate(self, messages, *, model=None, max_tokens=1024, system=None, tools=None) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(
            text=self._text,
            input_tokens=10,
            output_tokens=5,
            model=model or "fake-1",
            stop_reason="end_turn",
            elapsed_seconds=0.001,
        )

    def stream(self, messages, *, model=None, max_tokens=1024, system=None) -> Iterator[str]:
        self.calls.append(messages)
        yield from self._text.split()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fake_generate():
    client = FakeClient("hello world")
    resp = client.generate([Message(role="user", content="ping")])
    assert resp.text == "hello world"
    assert resp.input_tokens == 10
    assert resp.stop_reason == "end_turn"
    assert len(client.calls) == 1


def test_fake_stream():
    client = FakeClient("tok1 tok2 tok3")
    tokens = list(client.stream([Message(role="user", content="go")]))
    assert tokens == ["tok1", "tok2", "tok3"]


def test_fake_tool_calls():
    tc = ToolCall(id="1", name="search", input={"q": "test"})
    resp = LLMResponse(text="", tool_calls=[tc], stop_reason="tool_use")
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[0].input == {"q": "test"}


def test_factory_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    from llm.anthropic_client import AnthropicClient
    client = create_client()
    assert isinstance(client, AnthropicClient)


def test_factory_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    from llm.ollama_client import OllamaClient
    client = create_client()
    assert isinstance(client, OllamaClient)


def test_factory_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    from llm.openai_client import OpenAIClient
    client = create_client()
    assert isinstance(client, OpenAIClient)


def test_factory_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    from llm.gemini_client import GeminiClient
    client = create_client()
    assert isinstance(client, GeminiClient)


def test_factory_unknown(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gpt-99")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        create_client()


def test_exception_hierarchy():
    assert issubclass(ModelOverloadedError, ProviderError)
    assert issubclass(ContentBlockedError, ProviderError)
