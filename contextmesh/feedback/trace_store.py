"""ContextMesh trace store for PostgreSQL.

Records every compression event for ACON analysis and observability.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompressionTrace:
    """Record of a single compression event.

    Attributes:
        id: Unique trace identifier.
        session_id: Session this trace belongs to.
        task_id: Task this trace belongs to.
        tool_name: Tool that produced the output.
        chunk_ids_selected: IDs of chunks that were selected.
        chunk_ids_pruned: IDs of chunks that were pruned.
        original_token_count: Tokens before compression.
        compressed_token_count: Tokens after compression.
        compression_ratio: Ratio of compressed to original.
        chunk_types_selected: Types of selected chunks.
        chunk_types_pruned: Types of pruned chunks.
        metadata: Additional metadata.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    task_id: str = ""
    tool_name: str = ""
    chunk_ids_selected: list[str] = field(default_factory=list)
    chunk_ids_pruned: list[str] = field(default_factory=list)
    original_token_count: int = 0
    compressed_token_count: int = 0
    compression_ratio: float = 1.0
    chunk_types_selected: list[str] = field(default_factory=list)
    chunk_types_pruned: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TraceStore:
    """In-memory trace store (PostgreSQL integration TBD).

    Stores compression traces for ACON failure analysis.
    In production, this writes to PostgreSQL with pgvector
    for task description embedding similarity.

    Attributes:
        traces: List of compression traces.
        batch_size: Number of traces before flush.
    """

    def __init__(self, batch_size: int = 100) -> None:
        """Initialize trace store.

        Args:
            batch_size: Number of traces before flush.
        """
        self.traces: list[CompressionTrace] = []
        self.batch_size = batch_size

    def record(self, trace: CompressionTrace) -> None:
        """Record a compression event.

        Args:
            trace: Compression trace to record.
        """
        self.traces.append(trace)

        if len(self.traces) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Flush traces to persistent storage.

        In production, this writes to PostgreSQL.
        """
        logger.info(f"Flushing {len(self.traces)} traces")
        self.traces.clear()

    def get_traces_for_task(self, task_id: str) -> list[CompressionTrace]:
        """Get all traces for a task.

        Args:
            task_id: Task identifier.

        Returns:
            List of traces for the task.
        """
        return [t for t in self.traces if t.task_id == task_id]

    def get_traces_for_session(self, session_id: str) -> list[CompressionTrace]:
        """Get all traces for a session.

        Args:
            session_id: Session identifier.

        Returns:
            List of traces for the session.
        """
        return [t for t in self.traces if t.session_id == session_id]

    def get_all_traces(self) -> list[CompressionTrace]:
        """Get all recorded traces.

        Returns:
            List of all traces.
        """
        return list(self.traces)

    def get_stats(self) -> dict[str, Any]:
        """Get trace store statistics.

        Returns:
            Dictionary with stats.
        """
        if not self.traces:
            return {"trace_count": 0}

        total_original = sum(t.original_token_count for t in self.traces)
        total_compressed = sum(t.compressed_token_count for t in self.traces)

        return {
            "trace_count": len(self.traces),
            "avg_original_tokens": total_original / len(self.traces),
            "avg_compressed_tokens": total_compressed / len(self.traces),
            "avg_compression_ratio": total_compressed / total_original if total_original > 0 else 1.0,
        }
