"""ACON guideline adjustment for chunk scores.

Applies learned score multipliers from the ACON failure loop.
Guidelines are stored in PostgreSQL and loaded at startup.

Architecture:
    Raw scores -> guideline multiplier lookup -> adjusted scores
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextmesh.core.chunker.base import Chunk, ScoredChunk


Guideline = tuple[str, str]


class GuidelineStore:
    """In-memory store for extraction guidelines.

    Guidelines are (tool_name, chunk_type) -> score_multiplier mappings
    learned from the ACON failure loop.

    Attributes:
        guidelines: Map from (tool, chunk_type) to multiplier.
        max_multiplier: Maximum allowed multiplier value.
    """

    def __init__(self, max_multiplier: float = 3.0) -> None:
        """Initialize guideline store.

        Args:
            max_multiplier: Maximum multiplier cap.
        """
        self.max_multiplier = max_multiplier
        self.guidelines: dict[Guideline, float] = {}
        self._update_counts: dict[Guideline, int] = {}
        self._evidence: dict[Guideline, list[str]] = {}

    def set_guideline(
        self, tool_name: str, chunk_type: str, multiplier: float
    ) -> None:
        """Set or update a guideline multiplier.

        Args:
            tool_name: Tool name (e.g., "read_file").
            chunk_type: Chunk type (e.g., "function", "import_block").
            multiplier: Score multiplier (1.0 = no change).
        """
        key = (tool_name, chunk_type)
        capped = min(max(multiplier, 1.0), self.max_multiplier)
        self.guidelines[key] = capped

    def get_multiplier(self, tool_name: str, chunk_type: str) -> float:
        """Get multiplier for a tool/chunk_type pair.

        Args:
            tool_name: Tool name.
            chunk_type: Chunk type.

        Returns:
            Multiplier value (1.0 if no guideline).
        """
        key = (tool_name, chunk_type)
        return self.guidelines.get(key, 1.0)

    def increment_update_count(self, tool_name: str, chunk_type: str) -> None:
        """Track guideline update count.

        Args:
            tool_name: Tool name.
            chunk_type: Chunk type.
        """
        key = (tool_name, chunk_type)
        self._update_counts[key] = self._update_counts.get(key, 0) + 1

    def get_update_count(self, tool_name: str, chunk_type: str) -> int:
        """Get number of times a guideline was updated.

        Args:
            tool_name: Tool name.
            chunk_type: Chunk type.

        Returns:
            Update count.
        """
        key = (tool_name, chunk_type)
        return self._update_counts.get(key, 0)

    def add_evidence(self, tool_name: str, chunk_type: str, task_id: str) -> None:
        """Add evidence task ID to guideline.

        Args:
            tool_name: Tool name.
            chunk_type: Chunk type.
            task_id: Task ID that triggered this guideline.
        """
        key = (tool_name, chunk_type)
        if key not in self._evidence:
            self._evidence[key] = []
        if task_id not in self._evidence[key]:
            self._evidence[key].append(task_id)

    def get_evidence(self, tool_name: str, chunk_type: str) -> list[str]:
        """Get evidence task IDs for a guideline.

        Args:
            tool_name: Tool name.
            chunk_type: Chunk type.

        Returns:
            List of task IDs.
        """
        key = (tool_name, chunk_type)
        return self._evidence.get(key, [])

    def decay_multipliers(self, decay_factor: float = 0.95) -> None:
        """Apply decay to all multipliers.

        Reduces multipliers toward 1.0 when no failures occur.

        Args:
            decay_factor: Factor to multiply by (0.95 = 5% decay).
        """
        for key in list(self.guidelines.keys()):
            current = self.guidelines[key]
            if current > 1.0:
                new_val = max(1.0, current * decay_factor)
                self.guidelines[key] = new_val


class GuidelineAdjuster:
    """Applies ACON guideline adjustments to chunk scores.

    After initial relevance scoring, applies learned guidelines
    to boost scores for chunk types that have historically
    been pruned and caused failures.

    Attributes:
        store: The guideline store.

    Example:
        >>> adjuster = GuidelineAdjuster()
        >>> adjuster.store.set_guideline("read_file", "import_block", 1.8)
        >>> adjusted = adjuster.apply_guidelines(scored_chunks, "read_file")
    """

    def __init__(self, store: GuidelineStore | None = None) -> None:
        """Initialize guideline adjuster.

        Args:
            store: Optional guideline store (creates default if None).
        """
        self.store = store or GuidelineStore()

    def apply_guidelines(
        self, scored_chunks: list[ScoredChunk], tool_name: str
    ) -> list[ScoredChunk]:
        """Apply guideline multipliers to chunk scores.

        Args:
            scored_chunks: Chunks with raw relevance scores.
            tool_name: Name of the tool that produced these chunks.

        Returns:
            Chunks with adjusted scores.
        """
        result: list[ScoredChunk] = []

        for sc in scored_chunks:
            chunk_type = sc.chunk.chunk_type.value
            multiplier = self.store.get_multiplier(tool_name, chunk_type)

            if multiplier != 1.0:
                adjusted = ScoredChunk(
                    chunk=sc.chunk,
                    score=sc.score,
                    adjusted_score=sc.score * multiplier,
                )
                result.append(adjusted)
            else:
                result.append(sc)

        result.sort(key=lambda sc: sc.adjusted_score, reverse=True)
        return result

    def load_from_db(self, guidelines: dict[Guideline, float]) -> None:
        """Load guidelines from database.

        Args:
            guidelines: Dictionary of (tool, chunk_type) -> multiplier.
        """
        for (tool, chunk_type), multiplier in guidelines.items():
            self.store.set_guideline(tool, chunk_type, multiplier)
