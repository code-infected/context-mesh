"""LRU cache for chunk embeddings.

Provides efficient caching of chunk embeddings to avoid
re-computing embeddings for the same content.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass


class EmbeddingCache:
    """LRU cache for chunk embeddings.

    Caches embedding vectors keyed by chunk ID. Uses OrderedDict
    for O(1) LRU eviction.

    Attributes:
        max_size: Maximum number of entries.
        _cache: Internal cache storage.

    Example:
        >>> cache = EmbeddingCache(max_size=1000)
        >>> cache.put("chunk123", np.array([0.1, 0.2]))
        >>> vec = cache.get("chunk123")
    """

    def __init__(self, max_size: int = 10000) -> None:
        """Initialize embedding cache.

        Args:
            max_size: Maximum cache entries before eviction.
        """
        self.max_size = max_size
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> np.ndarray | None:
        """Get embedding from cache.

        Args:
            key: Chunk ID or content hash.

        Returns:
            Embedding vector or None if not found.
        """
        if key not in self._cache:
            self._misses += 1
            return None

        self._hits += 1
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, embedding: np.ndarray) -> None:
        """Store embedding in cache.

        Args:
            key: Chunk ID or content hash.
            embedding: Embedding vector.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = embedding
            return

        if len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = embedding

    def contains(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Key to check.

        Returns:
            True if key is cached.
        """
        return key in self._cache

    def clear(self) -> None:
        """Clear all cached entries and reset hit/miss counters."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def size(self) -> int:
        """Get current cache size.

        Returns:
            Number of entries in cache.
        """
        return len(self._cache)

    def hit_rate(self) -> float:
        """Calculate cache hit rate over all lookups since last clear.

        Returns:
            Hit rate as a float between 0 and 1 (0.0 before any lookup).
        """
        lookups = self._hits + self._misses
        return self._hits / lookups if lookups else 0.0

    def get_stats(self) -> dict[str, int | float]:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats.
        """
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "utilization": len(self._cache) / self.max_size if self.max_size > 0 else 0.0,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate(),
        }
