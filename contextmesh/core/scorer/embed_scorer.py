"""Embedding-based relevance scorer.

Uses sentence-transformers to compute relevance scores between
task context and chunks. Caches embeddings for efficiency.

sentence-transformers is an optional dependency (it pulls in torch).
When it is not installed, the scorer degrades to a deterministic
hashed bag-of-words encoder: scores become lexical-overlap similarity
instead of semantic similarity. This keeps the pipeline importable
and functional everywhere; install the "embeddings" extra for the
real model.

Architecture:
    Task context + chunks -> encoder (semantic or lexical fallback) ->
    cosine similarity -> relevance scores

Performance target: <30ms for 500 chunks
"""

from __future__ import annotations

import hashlib
import logging
import re
import zlib
from typing import Protocol

import numpy as np

from contextmesh.core.chunker.base import Chunk, ScoredChunk, TaskContext
from contextmesh.core.scorer.cache import EmbeddingCache

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

_FALLBACK_DIM = 512
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")


class _Encoder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


class LexicalFallbackEncoder:
    """Hashed bag-of-words encoder used when sentence-transformers is absent.

    Embeds text as an L2-normalized term-frequency vector over hashed
    word buckets. Cosine similarity between such vectors measures
    lexical overlap — much weaker than semantic embeddings, but
    deterministic, dependency-free, and fast.
    """

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode texts into normalized hashed-BoW vectors.

        Args:
            texts: Texts to encode.

        Returns:
            Array of shape (len(texts), dim), rows L2-normalized.
        """
        vectors = np.zeros((len(texts), _FALLBACK_DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in _WORD_RE.findall(text.lower()):
                bucket = zlib.crc32(word.encode()) % _FALLBACK_DIM
                vectors[row, bucket] += 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        np.divide(vectors, norms, out=vectors, where=norms > 0)
        return vectors


_MODEL_CACHE: dict[str, object] = {}


class _SentenceTransformerEncoder:
    """Thin adapter around a sentence-transformers model.

    Loaded models are cached process-wide: model loading takes seconds
    and multiple pipelines/scorers in one process (server + SDK + tests)
    must not each pay it.
    """

    def __init__(self, model_name: str) -> None:
        model = _MODEL_CACHE.get(model_name)
        if model is None:
            from sentence_transformers import SentenceTransformer

            # Cache-first, no network: hub metadata checks are both slow
            # and fragile under threaded servers (huggingface_hub's
            # shared HTTP client can be closed by another thread). Only
            # go online when the model isn't cached locally yet.
            try:
                model = SentenceTransformer(model_name, local_files_only=True)
            except Exception:
                logger.info("Model %s not in local cache; downloading", model_name)
                model = SentenceTransformer(model_name)
            _MODEL_CACHE[model_name] = model
        self._model = model

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        )


class EmbedScorer:
    """Relevance scorer over a shared embedding space.

    Embeds task context and chunk content into the same vector space,
    then computes cosine similarity as the relevance score. The encoder
    is loaded lazily on first use; if sentence-transformers is not
    installed, a lexical fallback encoder is used instead.

    Attributes:
        model_name: Name of the sentence-transformer model to use.
        cache: LRU cache for chunk embeddings, keyed by chunk ID.

    Example:
        >>> scorer = EmbedScorer()
        >>> task = TaskContext("fix auth bug", "read_file", {"path": "/src/auth.py"})
        >>> scored = scorer.score_chunks(chunks, task)
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        cache: EmbeddingCache | None = None,
        cache_size: int = 10000,
        embed_max_chars: int = 400,
        lexical_weight: float = 0.25,
    ) -> None:
        """Initialize embed scorer.

        Args:
            model_name: HuggingFace model name for the semantic encoder.
            cache: Optional pre-built embedding cache.
            cache_size: LRU capacity when building the default cache.
            embed_max_chars: Only the head of each chunk is embedded.
                Transformer encode time grows steeply with sequence
                length, and the relevance signal concentrates at chunk
                heads (signatures, docstrings, keys, headings).
            lexical_weight: Blend weight for a hashed-BoW lexical score
                alongside the semantic score. Embeddings are weak on
                exact identifiers ("user 42", error codes); the lexical
                component restores exact-match sensitivity. Ignored
                when the lexical fallback is already the primary encoder.
        """
        self.model_name = model_name
        self.cache = cache or EmbeddingCache(max_size=cache_size)
        self.embed_max_chars = embed_max_chars
        self.lexical_weight = min(max(lexical_weight, 0.0), 1.0)
        self._encoder: _Encoder | None = None
        self._lexical = LexicalFallbackEncoder()
        self._using_fallback = False

    @property
    def using_fallback(self) -> bool:
        """Whether the lexical fallback encoder is active (post-load)."""
        return self._using_fallback

    def warmup(self) -> None:
        """Load the encoder eagerly.

        Model loading can take seconds; callers with latency deadlines
        (the pipeline's hard timeout) call this once outside the timed
        path so the first real compression isn't aborted.
        """
        self._get_encoder()

    def _get_encoder(self) -> _Encoder:
        """Load the encoder lazily, falling back to lexical scoring."""
        if self._encoder is None:
            try:
                self._encoder = _SentenceTransformerEncoder(self.model_name)
            except ImportError:
                logger.warning(
                    "sentence-transformers is not installed; scoring with the "
                    "lexical fallback encoder. Install contextmesh[embeddings] "
                    "for semantic relevance scoring."
                )
                self._encoder = LexicalFallbackEncoder()
                self._using_fallback = True
        return self._encoder

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

        encoder = self._get_encoder()

        task_embedding = self._embed_task_context(task_context, encoder)

        # Batch-encode only cache misses: one encode() call, not N.
        # Only chunk heads are embedded (see embed_max_chars).
        missing_idx: list[int] = []
        embeddings: list[np.ndarray | None] = []
        for i, chunk in enumerate(chunks):
            cached = self.cache.get(chunk.id)
            embeddings.append(cached)
            if cached is None:
                missing_idx.append(i)

        if missing_idx:
            encoded = encoder.encode(
                [chunks[i].content[: self.embed_max_chars] for i in missing_idx]
            )
            for row, i in enumerate(missing_idx):
                embeddings[i] = encoded[row]
                self.cache.put(chunks[i].id, encoded[row])

        doc_matrix = np.stack(embeddings)  # type: ignore[arg-type]
        scores = doc_matrix @ task_embedding

        # Hybrid scoring: blend in exact-token lexical overlap over the
        # FULL chunk content, restoring sensitivity to identifiers that
        # embeddings blur and to content beyond the embedded head.
        if not self._using_fallback and self.lexical_weight > 0:
            lexical_scores = self._lexical_scores(chunks, task_context)
            scores = (1.0 - self.lexical_weight) * scores + (
                self.lexical_weight * lexical_scores
            )

        scored_chunks = [
            ScoredChunk(chunk=chunk, score=float(score))
            for chunk, score in zip(chunks, scores, strict=True)
        ]
        scored_chunks.sort(key=lambda sc: sc.score, reverse=True)
        return scored_chunks

    def _lexical_scores(
        self, chunks: list[Chunk], task_context: TaskContext
    ) -> np.ndarray:
        """Cosine similarity over hashed-BoW vectors (exact-token signal)."""
        missing_idx: list[int] = []
        vectors: list[np.ndarray | None] = []
        for i, chunk in enumerate(chunks):
            cached = self.cache.get("lex:" + chunk.id)
            vectors.append(cached)
            if cached is None:
                missing_idx.append(i)

        if missing_idx:
            encoded = self._lexical.encode([chunks[i].content for i in missing_idx])
            for row, i in enumerate(missing_idx):
                vectors[i] = encoded[row]
                self.cache.put("lex:" + chunks[i].id, encoded[row])

        task_vector = self._lexical.encode([task_context.to_string()])[0]
        return np.stack(vectors) @ task_vector  # type: ignore[arg-type]

    # Rolling task context weighting (spec failure mode 5): the original
    # task anchors relevance while recent steps track the agent's
    # current focus as multi-step tasks evolve.
    TASK_WEIGHT = 0.4
    STEPS_WEIGHT = 0.6

    def _embed_task_context(
        self, task_context: TaskContext, encoder: _Encoder
    ) -> np.ndarray:
        """Embed task context, weighting recent steps over the original task.

        The base task (description + tool + args) and the recent-steps
        text are embedded separately, combined 0.4/0.6, and renormalized.
        Each part is cached independently, so an unchanged task
        description stays cached while steps roll forward.

        Args:
            task_context: Task context object.
            encoder: The active encoder.

        Returns:
            Task embedding vector (unit norm when nonzero).
        """
        base_parts = [task_context.task_description, f"tool: {task_context.tool_name}"]
        if task_context.tool_args:
            args_str = ", ".join(f"{k}={v}" for k, v in task_context.tool_args.items())
            base_parts.append(f"args: {args_str}")
        base_text = " | ".join(base_parts)

        steps = [s for s in task_context.recent_steps[-3:] if s and s.strip()]
        if not steps:
            return self._embed_cached(base_text, encoder)

        base_embedding = self._embed_cached(base_text, encoder)
        steps_embedding = self._embed_cached(" | ".join(steps), encoder)

        combined = self.TASK_WEIGHT * base_embedding + self.STEPS_WEIGHT * steps_embedding
        norm = float(np.linalg.norm(combined))
        return combined / norm if norm > 0 else combined

    def _embed_cached(self, text: str, encoder: _Encoder) -> np.ndarray:
        """Embed a text with content-hash caching."""
        cache_key = "task:" + hashlib.sha256(text.encode()).hexdigest()[:16]

        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        embedding = encoder.encode([text])[0]
        self.cache.put(cache_key, embedding)
        return embedding

    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self.cache.clear()

    def get_cache_size(self) -> int:
        """Get number of cached embeddings.

        Returns:
            Number of entries in cache.
        """
        return self.cache.size()
