"""ACON guideline adjustment for chunk scores.

Applies learned score multipliers from the ACON failure loop.
Guidelines are stored in PostgreSQL and loaded at startup.

Architecture:
    Raw scores -> guideline multiplier lookup -> adjusted scores
"""

from __future__ import annotations

from datetime import UTC

from contextmesh.core.chunker.base import ScoredChunk

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
        self._last_updated: dict[Guideline, str] = {}

    def set_guideline(
        self, tool_name: str, chunk_type: str, multiplier: float
    ) -> None:
        """Set or update a guideline multiplier.

        Args:
            tool_name: Tool name (e.g., "read_file").
            chunk_type: Chunk type (e.g., "function", "import_block").
            multiplier: Score multiplier (1.0 = no change).
        """
        from datetime import datetime

        key = (tool_name, chunk_type)
        capped = min(max(multiplier, 1.0), self.max_multiplier)
        self.guidelines[key] = capped
        self._last_updated[key] = datetime.now(UTC).isoformat()

    def get_last_updated(self, tool_name: str, chunk_type: str) -> str | None:
        """ISO timestamp of the last update to a guideline, if any."""
        return self._last_updated.get((tool_name, chunk_type))

    def load_records(self, records: list[dict]) -> None:
        """Load guideline records (from persistence) into the store.

        Args:
            records: Dicts with tool_name, chunk_type, score_multiplier,
                update_count, evidence_task_ids, last_updated.
        """
        for r in records:
            key = (r["tool_name"], r["chunk_type"])
            multiplier = min(max(float(r["score_multiplier"]), 1.0), self.max_multiplier)
            self.guidelines[key] = multiplier
            self._update_counts[key] = int(r.get("update_count", 0))
            self._evidence[key] = list(r.get("evidence_task_ids", []))
            if r.get("last_updated"):
                self._last_updated[key] = r["last_updated"]

    def to_records(self) -> list[dict]:
        """Export guidelines as records for APIs and persistence.

        Returns:
            One dict per guideline with multiplier, update count,
            evidence, and last-updated timestamp.
        """
        records = []
        for (tool_name, chunk_type), multiplier in sorted(self.guidelines.items()):
            records.append(
                {
                    "tool_name": tool_name,
                    "chunk_type": chunk_type,
                    "score_multiplier": multiplier,
                    "update_count": self.get_update_count(tool_name, chunk_type),
                    "last_updated": self.get_last_updated(tool_name, chunk_type),
                    "evidence_task_ids": self.get_evidence(tool_name, chunk_type),
                }
            )
        return records

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

    def __init__(
        self,
        store: GuidelineStore | None = None,
        persistence: object | None = None,
        refresh_interval_s: float = 60.0,
    ) -> None:
        """Initialize guideline adjuster.

        Args:
            store: Optional guideline store (creates default if None).
            persistence: Optional feedback.guideline_persistence
                .GuidelinePersistence; when set, guidelines are
                refreshed from the database periodically so multiple
                processes converge on the same learned multipliers.
            refresh_interval_s: Seconds between persistence refreshes.
        """
        import threading

        self.store = store or GuidelineStore()
        self.persistence = persistence
        self.refresh_interval_s = refresh_interval_s
        self._last_refresh = 0.0
        self._refresh_lock = threading.Lock()

    def _maybe_refresh(self) -> None:
        """Reload guidelines from persistence when stale."""
        if self.persistence is None:
            return
        import time

        now = time.monotonic()
        if now - self._last_refresh < self.refresh_interval_s:
            return
        with self._refresh_lock:
            if now - self._last_refresh < self.refresh_interval_s:
                return
            self._last_refresh = now
        try:
            records = self.persistence.load_guidelines()
        except Exception:
            return
        if records:
            self.store.load_records(records)

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
        self._maybe_refresh()

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
