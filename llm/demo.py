"""Quick demo: run with LLM_PROVIDER=anthropic or LLM_PROVIDER=ollama."""
from __future__ import annotations

import os

from llm.factory import create_client
from llm.types import Message

PROMPT = "Reply in one sentence: what is retrieval-augmented generation?"


def main() -> None:
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    print(f"Provider: {provider}")

    client = create_client()

    # --- generate ---
    resp = client.generate([Message(role="user", content=PROMPT)])
    print(f"[generate] {resp.text}")
    print(f"  tokens in/out: {resp.input_tokens}/{resp.output_tokens}")
    print(f"  model: {resp.model}  stop: {resp.stop_reason}  time: {resp.elapsed_seconds:.2f}s")

    # --- stream ---
    print("[stream] ", end="", flush=True)
    for tok in client.stream([Message(role="user", content=PROMPT)]):
        print(tok, end="", flush=True)
    print()


if __name__ == "__main__":
    main()
