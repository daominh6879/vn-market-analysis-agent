"""
rag/retrieval_bm25.py — BM25 retriever over a Qdrant collection.

Loads all chunks at init, builds BM25Okapi index, exposes search().
Two tokenization modes:
  - raw split: text.split() — baseline
  - VN tokenize: underthesea.word_tokenize — handles compound words
"""
from __future__ import annotations

from qdrant_client import QdrantClient


def _raw_tokenize(text: str) -> list[str]:
    return text.lower().split()


def _vn_tokenize(text: str) -> list[str]:
    from underthesea import word_tokenize
    return word_tokenize(text.lower(), format="text").split()


class BM25Retriever:
    def __init__(
        self,
        collection: str,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        use_vn_tokenize: bool = False,
    ) -> None:
        from rank_bm25 import BM25Okapi

        self._tokenize = _vn_tokenize if use_vn_tokenize else _raw_tokenize

        client = QdrantClient(qdrant_host, port=qdrant_port)
        self._texts = self._scroll_all(client, collection)
        print(f"  BM25: loaded {len(self._texts)} chunks from '{collection}' "
              f"({'vn_tokenize' if use_vn_tokenize else 'raw_split'})")

        tokenized = [self._tokenize(t) for t in self._texts]
        self._bm25 = BM25Okapi(tokenized)

    @staticmethod
    def _scroll_all(client: QdrantClient, collection: str) -> list[str]:
        texts: list[str] = []
        offset = None
        while True:
            results, offset = client.scroll(
                collection_name=collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in results:
                text = point.payload.get("text", "")
                if text:
                    texts.append(text)
            if offset is None:
                break
        return texts

    def search(self, query: str, top_k: int = 5) -> list[str]:
        tokens = self._tokenize(query)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._texts[i] for i in ranked[:top_k]]

    def search_scored(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Return (text, bm25_score) pairs sorted descending — for fusion."""
        tokens = self._tokenize(query)
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(self._texts[i], float(scores[i])) for i in ranked[:top_k]]
