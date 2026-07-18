"""Vector search over the FAA orders indexed by vector_db/ingest.py.

Accepts multiple topics per call (see vector_db_setup_justification.md's example:
topics=["RECAT Phase II Category B leading Category E", "NTZ breakout lateral divergence
minimums"]) — one search per topic, results deduplicated by chunk and returned with their
section citation so the caller can trace every retrieved rule back to its source paragraph.
"""

import json
from pathlib import Path

import faiss
from sentence_transformers import SentenceTransformer

VECTOR_DB_DIR = Path(__file__).parent.parent / "vector_db"
INDEX_PATH = VECTOR_DB_DIR / "faa_rules.index"
CHUNKS_PATH = VECTOR_DB_DIR / "chunks.jsonl"

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TOP_K_PER_TOPIC = 3


class FaaRulesIndex:
    """Loads the FAISS index once; query_faa_rules() reuses this across calls."""

    def __init__(self):
        if not INDEX_PATH.exists() or not CHUNKS_PATH.exists():
            raise FileNotFoundError(
                f"{INDEX_PATH} / {CHUNKS_PATH} not found — run "
                "`docker compose run --rm mcp_server python vector_db/ingest.py` first"
            )
        self._model = SentenceTransformer(EMBEDDING_MODEL)
        self._index = faiss.read_index(str(INDEX_PATH))
        self._chunks: list[dict] = []
        with open(CHUNKS_PATH, encoding="utf-8") as f:
            for line in f:
                self._chunks.append(json.loads(line))

    def search(self, topics: list[str], top_k: int = TOP_K_PER_TOPIC) -> list[dict]:
        if not topics:
            return []

        query_vectors = self._model.encode(topics, normalize_embeddings=True)
        seen_chunk_ids: set[int] = set()
        results: list[dict] = []

        for topic, query_vector in zip(topics, query_vectors):
            scores, indices = self._index.search(query_vector.reshape(1, -1).astype("float32"), top_k)
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0:
                    continue
                chunk = self._chunks[idx]
                if chunk["chunk_id"] in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(chunk["chunk_id"])
                results.append(
                    {
                        "matched_topic": topic,
                        "score": float(score),
                        "doc_id": chunk["doc_id"],
                        "doc_title": chunk["doc_title"],
                        "section_id": chunk["section_id"],
                        "heading": chunk["heading"],
                        "text": chunk["text"],
                    }
                )

        results.sort(key=lambda r: r["score"], reverse=True)
        return results


def query_faa_rules(topics: list[str], index: FaaRulesIndex) -> list[dict]:
    """MCP tool entry point. `index` is injected by server.py, which owns the singleton
    (loading the embedding model + FAISS index per-call would be needlessly slow)."""
    return index.search(topics)
