"""CLI demo for HybridMemoryAgent — test topic switching across sessions."""
import sys
import os

# Run from project root: python scripts/chat_context_demo.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from memory.chat_context import HybridMemoryAgent

CONVERSATION_ID = "demo"
USER_ID = "demo_user"


def main():
    print("Hybrid Memory Agent — type 'quit' to exit, 'clear' to wipe memory\n")
    agent = HybridMemoryAgent(conversation_id=CONVERSATION_ID, user_id=USER_ID)

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            break
        if user_input.lower() == "clear":
            agent.clear(CONVERSATION_ID)
            print("Memory cleared.\n")
            continue

        reply = agent.chat(user_input)
        print(f"AI: {reply}\n")


if __name__ == "__main__":
    main()
