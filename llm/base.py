from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from llm.types import LLMResponse, Message


class LLMClient(ABC):
    @abstractmethod
    def generate(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> Iterator[str]: ...
