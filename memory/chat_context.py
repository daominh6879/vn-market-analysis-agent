import threading
import time
import uuid
from collections import deque
from typing import Optional

from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

from llm import Message, create_client

_SYSTEM_TEMPLATE = """You are a financial assistant with memory of past conversations.

[Long-Term Context - Retrieved]
{retrieved}

[Recent Conversation]
{recent}"""

_PERSIST_DIR = "./chroma_db"
_COLLECTION = "chat_history"
_MAX_DOCS = 1000
_PRUNE_BATCH = 100


def _make_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(model="nomic-embed-text")


def _get_chroma(persist_dir: str = _PERSIST_DIR) -> Chroma:
    return Chroma(
        collection_name=_COLLECTION,
        embedding_function=_make_embeddings(),
        persist_directory=persist_dir,
    )


# ── Module-level singleton — shared by turn_handler and deletion hook ──────────

_chroma_singleton: Optional[Chroma] = None


def _get_singleton(persist_dir: str = _PERSIST_DIR) -> Chroma:
    global _chroma_singleton
    if _chroma_singleton is None:
        _chroma_singleton = _get_chroma(persist_dir)
    return _chroma_singleton


_prune_lock = threading.Lock()


def _prune_collection(chroma: Chroma, max_docs: int) -> None:
    """Delete oldest _PRUNE_BATCH docs when collection exceeds max_docs."""
    with _prune_lock:
        count = chroma._collection.count()
        if count <= max_docs:
            return
        result = chroma._collection.get(include=["metadatas"])
        pairs = sorted(
            zip(result["ids"], result["metadatas"]),
            key=lambda x: x[1].get("created_at", 0),
        )
        to_delete = [id_ for id_, _ in pairs[:_PRUNE_BATCH]]
        if to_delete:
            chroma._collection.delete(ids=to_delete)


class HybridMemoryAgent:
    """Hybrid memory chat agent: sliding window (last 4 turns) + ChromaDB long-term retrieval."""

    def __init__(
        self,
        conversation_id: str,
        user_id: str,
        persist_dir: str = _PERSIST_DIR,
        max_docs: int = _MAX_DOCS,
    ):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.max_docs = max_docs
        self._chroma = _get_singleton()
        # Sliding window: 4 turns × 2 messages (user + assistant) = maxlen 8
        # Resets on restart — ChromaDB carries persistence across sessions
        self._buffer: deque[dict] = deque(maxlen=8)
        self._llm = create_client()

    def chat(self, user_message: str) -> str:
        """5-step pipeline: retrieve → window → assemble → generate → store."""
        # Step 1: vector retrieval — top 3 semantically similar past turns from ChromaDB
        # Filter by user_id so users never see each other's conversation context
        results = self._chroma.similarity_search(
            user_message, k=3, filter={"user_id": self.user_id}
        )
        retrieved_texts = [doc.page_content for doc in results]

        # Step 2: sliding window — last 4 turns from in-memory buffer
        # Buffer is fast but volatile; ChromaDB is the durable store
        recent = list(self._buffer)

        # Step 3: assemble prompt with retrieved long-term context + recent window
        retrieved_str = "\n---\n".join(retrieved_texts) if retrieved_texts else "(none yet)"
        recent_str = (
            "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in recent)
            if recent
            else "(none yet)"
        )
        system = _SYSTEM_TEMPLATE.format(retrieved=retrieved_str, recent=recent_str)

        # Step 4: generate via project LLM factory (respects LLM_PROVIDER env)
        response = self._llm.generate(
            messages=[Message(role="user", content=user_message)],
            system=system,
            max_tokens=2048,
        )
        ai_reply = response.text.strip()

        if not ai_reply:
            return ai_reply

        # Step 5: persist to both stores
        # Sliding window (in-memory) — immediate context for next turn
        self._buffer.append({"role": "user", "content": user_message})
        self._buffer.append({"role": "assistant", "content": ai_reply})

        # ChromaDB (durable) — use module-level chroma_store so all writes/prunes share one client
        chroma_store(self.conversation_id, self.user_id, user_message, ai_reply)

        return ai_reply

    def delete_by_conversation(self, conversation_id: str) -> None:
        """Remove all ChromaDB docs belonging to a conversation."""
        self._chroma._collection.delete(where={"conversation_id": conversation_id})

    def clear(self, conversation_id: Optional[str] = None) -> None:
        """Wipe all docs (conversation_id=None) or filter to one conversation."""
        if conversation_id is None:
            result = self._chroma._collection.get()
            if result["ids"]:
                self._chroma._collection.delete(ids=result["ids"])
        else:
            self._chroma._collection.delete(where={"conversation_id": conversation_id})


# ── Standalone functions used by turn_handler (sync) and conversation.py ──────

def chroma_retrieve(query: str, user_id: str, top_k: int = 3) -> list[str]:
    """Retrieve top_k semantically similar past turns for this user. Called each turn."""
    chroma = _get_singleton()
    results = chroma.similarity_search(query, k=top_k, filter={"user_id": user_id})
    return [doc.page_content for doc in results]


def chroma_store(
    conversation_id: str,
    user_id: str,
    user_msg: str,
    ai_reply: str,
) -> None:
    """Persist one turn to ChromaDB. Called after save_turn() by turn_handler."""
    if not ai_reply:
        return
    chroma = _get_singleton()
    chroma.add_texts(
        texts=[f"User: {user_msg}\nAssistant: {ai_reply}"],
        metadatas=[{
            "conversation_id": conversation_id,
            "user_id": user_id,
            "created_at": int(time.time()),
        }],
        ids=[str(uuid.uuid4())],
    )
    _prune_collection(chroma, _MAX_DOCS)


def delete_conversation_context(conversation_id: str) -> None:
    """Deletion hook called by conversation.py on conversation delete.
    Uses the singleton so the same client instance handles writes and deletes."""
    chroma = _get_singleton()
    chroma._collection.delete(where={"conversation_id": conversation_id})
