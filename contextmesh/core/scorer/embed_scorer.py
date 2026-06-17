"""Embedding-based relevance scorer.

Uses sentence-transformers to compute relevance scores between
task context and chunks. Caches embeddings for efficiency.

Architecture:
    Task context + chunks -> embedding model ->
    cosine similarity -> relevance scores

Performance target: <30ms for 500 chunks
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from sentence_transformers import SentenceTransformer

from contextmesh.core.chunker.base import Chunk, ScoredChunk, TaskContext

if TYPE_CHECKING:
    from collections.abc import Mapping


DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class EmbedScorer:
    """Sentence-transformer based relevance scorer.

    Embeds task context and chunk content into the same vector space,
    then computes cosine similarity as the relevance score.

    Attributes:
        model: The sentence-transformer model instance.
        cache: Optional external cache for embeddings.
        model_name: Name of the model being used.

    Example:
        >>> scorer = EmbedScorer()
        >>> chunks = [Chunk(...)]
        >>> task = TaskContext("fix auth bug", "read_file", {"path": "/src/auth.py"})
        >>> scored = scorer.score_chunks(chunks, task)
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        cache: Mapping[str, np.ndarray] | None = None,
    ) -> None:
        """Initialize embed scorer.

        Args:
            model_name: HuggingFace model name.
            cache: Optional embedding cache.
        """
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.cache: dict[str, np.ndarray] = dict(cache) if cache else {}

    def score_chunks(
        self, chunks: list[Chunk], task_context: TaskContext
    ) -> list[ScoredChunk]:
        """Score chunks for relevance to task context.

        Args:
            chunks: Chunks to score.
            task_context: Task and agent context.

        Returns:
            List of scored chunks sorted by score descending.
        """
        if not chunks:
            return []

        task_embedding = self._embed_task_context(task_context)

        chunk_embeddings: list[np.ndarray] = []
        for chunk in chunks:
            emb = self.cache.get(chunk.id)
            if emb is None:
                emb = self._embed_text(chunk.content)
                self.cache[chunk.id] = emb
            chunk_embeddings.append(emb)

        scores = self._cosine_similarity(task_embedding, chunk_embeddings)

        scored_chunks = [
            ScoredChunk(chunk=chunk, score=float(score))
            for chunk, score in zip(chunks, scores)
        ]

        scored_chunks.sort(key=lambda sc: sc.score, reverse=True)
        return scored_chunks

    def _embed_task_context(self, task_context: TaskContext) -> np.ndarray:
        """Embed task context string.

        Args:
            task_context: Task context object.

        Returns:
            Task embedding vector.
        """
        text = task_context.to_string()
        cache_key = f"task:{hash(text)}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        embedding = self._embed_text(text)
        self.cache[cache_key] = embedding

        return embedding

    def _embed_text(self, text: str) -> np.ndarray:
        """Embed a text string.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector.
        """
        embedding = self.model.encode(text, normalize_embeddings=True)
        return embedding

    def _cosine_similarity(
        self, query: np.ndarray, documents: list[np.ndarray]
    ) -> np.ndarray:
        """Compute cosine similarity between query and documents.

        Args:
            query: Query embedding (1D array).
            documents: Document embeddings (list of 1D arrays).

        Returns:
            Array of similarity scores.
        """
        doc_matrix = np.stack(documents)
        similarities = np.dot(doc_matrix, query)
        return similarities

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self.cache.clear()

    def get_cache_size(self) -> int:
        """Get number of cached embeddings.

        Returns:
            Number of entries in cache.
        """
        return len(self.cache)
