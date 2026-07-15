"""ContextMesh trace store.

Records every compression event for ACON analysis and observability.

Two backends:

    InMemoryBackend  — default; bounded ring buffer, used for tests,
                       local development, and as the fail-open fallback.
    PostgresBackend  — used when a database URL is configured and
                       psycopg2 can connect; writes compression_traces
                       per feedback/schema.sql.

The store always fails open: if the database is unreachable, traces
are kept in memory and a warning is logged. Compression must never
block on trace persistence.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class CompressionTrace:
    """Record of a single compression event.

    Attributes:
        id: Unique trace identifier.
        session_id: Session this trace belongs to.
        task_id: Task this trace belongs to.
        tool_name: Tool that produced the output.
        tool_args_hash: Hash of the tool arguments for grouping.
        chunk_ids_selected: IDs of chunks that were selected.
        chunk_ids_pruned: IDs of chunks that were pruned.
        original_token_count: Tokens before compression.
        compressed_token_count: Tokens after compression.
        compression_ratio: Ratio of compressed to original.
        chunk_types_selected: Types of selected chunks.
        chunk_types_pruned: Types of pruned chunks.
        low_signal: Scorer flagged near-uniform scores (vague task).
        created_at: ISO-8601 UTC timestamp.
        metadata: Additional metadata.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    task_id: str = ""
    tool_name: str = ""
    tool_args_hash: str = ""
    chunk_ids_selected: list[str] = field(default_factory=list)
    chunk_ids_pruned: list[str] = field(default_factory=list)
    original_token_count: int = 0
    compressed_token_count: int = 0
    compression_ratio: float = 1.0
    chunk_types_selected: list[str] = field(default_factory=list)
    chunk_types_pruned: list[str] = field(default_factory=list)
    low_signal: bool = False
    created_at: str = field(default_factory=_utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "trace_id": self.id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "timestamp": self.created_at,
            "original_tokens": self.original_token_count,
            "compressed_tokens": self.compressed_token_count,
            "compression_ratio": self.compression_ratio,
            "chunks_selected": len(self.chunk_ids_selected),
            "chunks_total": len(self.chunk_ids_selected) + len(self.chunk_ids_pruned),
            "chunk_types_selected": self.chunk_types_selected,
            "low_signal": self.low_signal,
        }


class InMemoryBackend:
    """Bounded in-memory trace storage."""

    def __init__(self, max_traces: int = 10000) -> None:
        self.max_traces = max_traces
        self._traces: list[CompressionTrace] = []
        self._lock = threading.Lock()

    def write(self, traces: list[CompressionTrace]) -> None:
        with self._lock:
            self._traces.extend(traces)
            overflow = len(self._traces) - self.max_traces
            if overflow > 0:
                del self._traces[:overflow]

    def all_traces(self) -> list[CompressionTrace]:
        with self._lock:
            return list(self._traces)


class PostgresBackend:
    """PostgreSQL trace storage via psycopg2.

    Connections are opened lazily; any failure downgrades the caller
    to in-memory behavior for that operation (fail-open).
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._conn: Any = None
        self._lock = threading.Lock()

    def _connection(self) -> Any:
        import psycopg2

        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.database_url)
            self._conn.autocommit = True
        return self._conn

    def write(self, traces: list[CompressionTrace]) -> None:
        with self._lock:
            conn = self._connection()
            with conn.cursor() as cur:
                for t in traces:
                    cur.execute(
                        """
                        INSERT INTO compression_traces (
                            id, session_id, task_id, tool_name, tool_args_hash,
                            chunk_ids_selected, chunk_ids_pruned,
                            original_token_count, compressed_token_count,
                            compression_ratio, chunk_types_selected,
                            chunk_types_pruned, low_signal, metadata, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            t.id, t.session_id, t.task_id, t.tool_name,
                            t.tool_args_hash, t.chunk_ids_selected,
                            t.chunk_ids_pruned, t.original_token_count,
                            t.compressed_token_count, t.compression_ratio,
                            t.chunk_types_selected, t.chunk_types_pruned,
                            t.low_signal, json.dumps(t.metadata), t.created_at,
                        ),
                    )

    def all_traces(self) -> list[CompressionTrace]:
        with self._lock:
            conn = self._connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, session_id, task_id, tool_name, tool_args_hash,
                           chunk_ids_selected, chunk_ids_pruned,
                           original_token_count, compressed_token_count,
                           compression_ratio, chunk_types_selected,
                           chunk_types_pruned, low_signal, metadata, created_at
                    FROM compression_traces
                    ORDER BY created_at
                    """
                )
                rows = cur.fetchall()

        traces = []
        for row in rows:
            traces.append(
                CompressionTrace(
                    id=row[0], session_id=row[1], task_id=row[2],
                    tool_name=row[3], tool_args_hash=row[4] or "",
                    chunk_ids_selected=list(row[5] or []),
                    chunk_ids_pruned=list(row[6] or []),
                    original_token_count=row[7],
                    compressed_token_count=row[8],
                    compression_ratio=row[9],
                    chunk_types_selected=list(row[10] or []),
                    chunk_types_pruned=list(row[11] or []),
                    low_signal=bool(row[12]),
                    metadata=row[13] if isinstance(row[13], dict) else {},
                    created_at=row[14].isoformat() if row[14] else _utcnow(),
                )
            )
        return traces


class TraceStore:
    """Facade over trace storage backends.

    Traces are buffered and flushed to the backend in batches. When a
    database URL is provided and reachable, the PostgreSQL backend is
    used; otherwise everything stays in memory.

    Attributes:
        batch_size: Buffered traces before an automatic flush.
    """

    def __init__(
        self,
        database_url: str | None = None,
        batch_size: int = 100,
        max_memory_traces: int = 10000,
    ) -> None:
        """Initialize trace store.

        Args:
            database_url: PostgreSQL URL; empty/None selects in-memory.
            batch_size: Buffered traces before an automatic flush.
            max_memory_traces: Ring-buffer cap for the in-memory backend.
        """
        self.batch_size = batch_size
        self._buffer: list[CompressionTrace] = []
        self._lock = threading.Lock()
        self._memory = InMemoryBackend(max_traces=max_memory_traces)
        self._postgres: PostgresBackend | None = None
        self._postgres_failures = 0

        if database_url:
            try:
                import psycopg2  # noqa: F401

                self._postgres = PostgresBackend(database_url)
            except ImportError:
                logger.warning(
                    "psycopg2 is not installed; trace store falling back to memory"
                )

    _MAX_POSTGRES_FAILURES = 3

    def _postgres_failed(self, error: Exception, context: str) -> None:
        """Count a backend failure; disable PostgreSQL after repeats."""
        self._postgres_failures += 1
        logger.warning("Trace store PostgreSQL %s failed: %s", context, error)
        if self._postgres_failures >= self._MAX_POSTGRES_FAILURES:
            logger.warning(
                "Disabling PostgreSQL trace backend after %d consecutive "
                "failures; traces stay in memory", self._postgres_failures,
            )
            self._postgres = None

    @property
    def backend_name(self) -> str:
        """Name of the active persistent backend."""
        return "postgresql" if self._postgres is not None else "memory"

    def record(self, trace: CompressionTrace) -> None:
        """Record a compression event.

        Args:
            trace: Compression trace to record.
        """
        with self._lock:
            self._buffer.append(trace)
            should_flush = len(self._buffer) >= self.batch_size
        if should_flush:
            self.flush()

    def flush(self) -> None:
        """Flush buffered traces to the active backend.

        Never raises: on database failure, traces land in memory and a
        warning is logged (fail-open).
        """
        with self._lock:
            batch = self._buffer
            self._buffer = []

        if not batch:
            return

        if self._postgres is not None:
            try:
                self._postgres.write(batch)
                self._postgres_failures = 0
                return
            except Exception as e:
                self._postgres_failed(e, f"flush of {len(batch)} traces")

        self._memory.write(batch)

    def get_all_traces(self) -> list[CompressionTrace]:
        """All recorded traces (buffered + persisted)."""
        persisted: list[CompressionTrace] = []
        if self._postgres is not None:
            try:
                persisted = self._postgres.all_traces()
                self._postgres_failures = 0
            except Exception as e:
                self._postgres_failed(e, "read")
        persisted.extend(self._memory.all_traces())
        with self._lock:
            persisted.extend(self._buffer)
        return persisted

    def get_traces_for_task(self, task_id: str) -> list[CompressionTrace]:
        """All traces for a task."""
        return [t for t in self.get_all_traces() if t.task_id == task_id]

    def get_traces_for_session(self, session_id: str) -> list[CompressionTrace]:
        """All traces for a session."""
        return [t for t in self.get_all_traces() if t.session_id == session_id]

    def session_summaries(self) -> list[dict[str, Any]]:
        """Per-session aggregate stats for the dashboard."""
        sessions: dict[str, dict[str, Any]] = {}
        for t in self.get_all_traces():
            s = sessions.setdefault(
                t.session_id,
                {
                    "session_id": t.session_id,
                    "task_ids": set(),
                    "trace_count": 0,
                    "original_tokens": 0,
                    "compressed_tokens": 0,
                    "first_seen": t.created_at,
                    "last_seen": t.created_at,
                },
            )
            s["task_ids"].add(t.task_id)
            s["trace_count"] += 1
            s["original_tokens"] += t.original_token_count
            s["compressed_tokens"] += t.compressed_token_count
            s["first_seen"] = min(s["first_seen"], t.created_at)
            s["last_seen"] = max(s["last_seen"], t.created_at)

        result = []
        for s in sessions.values():
            original = s["original_tokens"]
            compressed = s["compressed_tokens"]
            result.append(
                {
                    "session_id": s["session_id"],
                    "task_count": len(s["task_ids"]),
                    "trace_count": s["trace_count"],
                    "original_tokens": original,
                    "compressed_tokens": compressed,
                    "tokens_saved": original - compressed,
                    "avg_compression_ratio": (
                        compressed / original if original > 0 else 1.0
                    ),
                    "first_seen": s["first_seen"],
                    "last_seen": s["last_seen"],
                }
            )
        result.sort(key=lambda s: s["last_seen"], reverse=True)
        return result

    def tool_stats(self) -> list[dict[str, Any]]:
        """Per-tool aggregate stats for the dashboard."""
        tools: dict[str, dict[str, Any]] = {}
        for t in self.get_all_traces():
            s = tools.setdefault(
                t.tool_name,
                {
                    "tool_name": t.tool_name,
                    "call_count": 0,
                    "original_tokens": 0,
                    "compressed_tokens": 0,
                    "chunks_selected": 0,
                    "chunks_total": 0,
                },
            )
            s["call_count"] += 1
            s["original_tokens"] += t.original_token_count
            s["compressed_tokens"] += t.compressed_token_count
            s["chunks_selected"] += len(t.chunk_ids_selected)
            s["chunks_total"] += len(t.chunk_ids_selected) + len(t.chunk_ids_pruned)

        result = []
        for s in tools.values():
            calls = s["call_count"]
            original = s["original_tokens"]
            compressed = s["compressed_tokens"]
            result.append(
                {
                    "tool_name": s["tool_name"],
                    "call_count": calls,
                    "avg_compression_ratio": (
                        compressed / original if original > 0 else 1.0
                    ),
                    "original_tokens": original,
                    "compressed_tokens": compressed,
                    "tokens_saved": original - compressed,
                    "avg_chunks_selected": s["chunks_selected"] / calls if calls else 0,
                    "avg_chunks_total": s["chunks_total"] / calls if calls else 0,
                }
            )
        result.sort(key=lambda s: s["call_count"], reverse=True)
        return result

    def get_stats(self) -> dict[str, Any]:
        """Aggregate trace store statistics."""
        traces = self.get_all_traces()
        if not traces:
            return {"trace_count": 0, "backend": self.backend_name}

        total_original = sum(t.original_token_count for t in traces)
        total_compressed = sum(t.compressed_token_count for t in traces)

        return {
            "trace_count": len(traces),
            "backend": self.backend_name,
            "sessions": len({t.session_id for t in traces}),
            "avg_original_tokens": total_original / len(traces),
            "avg_compressed_tokens": total_compressed / len(traces),
            "avg_compression_ratio": (
                total_compressed / total_original if total_original > 0 else 1.0
            ),
            "total_tokens_saved": total_original - total_compressed,
        }
