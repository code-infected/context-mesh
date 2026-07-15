"""PostgreSQL persistence for the ACON feedback state.

Persists extraction guidelines, their audit history, task outcomes,
and analyzed failures (the extraction_guidelines, guideline_history,
task_outcomes, and failed_tasks tables from schema.sql), so learned
multipliers survive restarts.

Fail-open like the trace store: any database error is logged and the
caller continues on in-memory state; after repeated consecutive
failures the backend disables itself.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_MAX_FAILURES = 3


class GuidelinePersistence:
    """PostgreSQL persistence for guidelines and failure records.

    Attributes:
        database_url: PostgreSQL connection URL.
        available: False once disabled by repeated failures (or when
            psycopg2 is missing).
    """

    def __init__(self, database_url: str) -> None:
        """Initialize persistence.

        Args:
            database_url: PostgreSQL connection URL.
        """
        self.database_url = database_url
        self.available = True
        self._conn: Any = None
        self._failures = 0
        self._lock = threading.Lock()

        try:
            import psycopg2  # noqa: F401
        except ImportError:
            logger.warning("psycopg2 not installed; guideline persistence disabled")
            self.available = False

    def _connection(self) -> Any:
        import psycopg2

        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.database_url)
            self._conn.autocommit = True
        return self._conn

    def _execute(self, description: str, fn: Any) -> Any:
        """Run a DB operation fail-open; disable after repeated failures."""
        if not self.available:
            return None
        with self._lock:
            try:
                result = fn(self._connection())
                self._failures = 0
                return result
            except Exception as e:
                self._failures += 1
                logger.warning("Guideline persistence %s failed: %s", description, e)
                if self._failures >= _MAX_FAILURES:
                    logger.warning(
                        "Disabling guideline persistence after %d consecutive failures",
                        self._failures,
                    )
                    self.available = False
                return None

    # ------------------------------------------------------------------
    # Guidelines

    def upsert_guideline(self, record: dict[str, Any]) -> None:
        """Insert or update one guideline record.

        Args:
            record: Dict with tool_name, chunk_type, score_multiplier,
                update_count, evidence_task_ids.
        """
        def op(conn: Any) -> None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO extraction_guidelines
                        (id, tool_name, chunk_type, score_multiplier,
                         update_count, evidence_task_ids, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (tool_name, chunk_type) DO UPDATE SET
                        score_multiplier = EXCLUDED.score_multiplier,
                        update_count = EXCLUDED.update_count,
                        evidence_task_ids = EXCLUDED.evidence_task_ids,
                        last_updated = NOW()
                    """,
                    (
                        str(uuid.uuid4()),
                        record["tool_name"],
                        record["chunk_type"],
                        float(record["score_multiplier"]),
                        int(record.get("update_count", 0)),
                        list(record.get("evidence_task_ids", [])),
                    ),
                )

        self._execute("guideline upsert", op)

    def load_guidelines(self) -> list[dict[str, Any]]:
        """Load all persisted guidelines.

        Returns:
            Guideline records ([] when unavailable or empty).
        """
        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tool_name, chunk_type, score_multiplier,
                           update_count, evidence_task_ids, last_updated
                    FROM extraction_guidelines
                    """
                )
                rows = cur.fetchall()
            return [
                {
                    "tool_name": r[0],
                    "chunk_type": r[1],
                    "score_multiplier": float(r[2]),
                    "update_count": int(r[3]),
                    "evidence_task_ids": list(r[4] or []),
                    "last_updated": r[5].isoformat() if r[5] else None,
                }
                for r in rows
            ]

        return self._execute("guideline load", op) or []

    def record_history(self, update: dict[str, Any]) -> None:
        """Append a guideline change to the audit trail.

        Args:
            update: Dict with tool_name, chunk_type, old_multiplier,
                new_multiplier, task_id.
        """
        def op(conn: Any) -> None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO guideline_history
                        (id, tool_name, chunk_type, old_multiplier,
                         new_multiplier, trigger_task_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        update["tool_name"],
                        update["chunk_type"],
                        float(update["old_multiplier"]),
                        float(update["new_multiplier"]),
                        update.get("task_id"),
                    ),
                )

        self._execute("history insert", op)

    def load_history(self, limit: int = 500) -> list[dict[str, Any]]:
        """Load guideline history, newest first."""
        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tool_name, chunk_type, old_multiplier,
                           new_multiplier, trigger_task_id, created_at
                    FROM guideline_history
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
            return [
                {
                    "tool_name": r[0],
                    "chunk_type": r[1],
                    "old_multiplier": float(r[2]),
                    "new_multiplier": float(r[3]),
                    "task_id": r[4] or "",
                    "timestamp": r[5].isoformat() if r[5] else "",
                }
                for r in rows
            ]

        return self._execute("history load", op) or []

    # ------------------------------------------------------------------
    # Outcomes / failures

    def record_outcome(self, event: dict[str, Any]) -> None:
        """Persist a reported task outcome (idempotent per task)."""
        def op(conn: Any) -> None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO task_outcomes
                        (id, task_id, session_id, outcome, failure_reason,
                         evaluation_score, agent_final_output)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (task_id) DO UPDATE SET
                        outcome = EXCLUDED.outcome,
                        failure_reason = EXCLUDED.failure_reason,
                        evaluation_score = EXCLUDED.evaluation_score
                    """,
                    (
                        str(uuid.uuid4()),
                        event["task_id"],
                        event.get("session_id"),
                        event.get("outcome", "unknown"),
                        event.get("failure_reason"),
                        event.get("evaluation_score"),
                        event.get("agent_final_output"),
                    ),
                )

        self._execute("outcome insert", op)

    def record_failed_task(self, record: dict[str, Any]) -> None:
        """Persist an analyzed failure record."""
        def op(conn: Any) -> None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO failed_tasks
                        (id, task_id, session_id, failure_reason,
                         compression_implicated, root_cause_chunks,
                         root_cause_chunk_types, analysis_completed,
                         completed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW())
                    """,
                    (
                        str(uuid.uuid4()),
                        record["task_id"],
                        record.get("session_id"),
                        record.get("failure_reason", ""),
                        bool(record.get("compression_implicated", False)),
                        list(record.get("trace_ids", [])),
                        list(record.get("pruned_chunk_types", [])),
                    ),
                )

        self._execute("failed-task insert", op)

    def load_failures(self, limit: int = 500) -> list[dict[str, Any]]:
        """Load analyzed failures, newest first."""
        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT task_id, session_id, failure_reason,
                           compression_implicated, root_cause_chunks,
                           root_cause_chunk_types, created_at
                    FROM failed_tasks
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
            return [
                {
                    "task_id": r[0],
                    "session_id": r[1] or "",
                    "failure_reason": r[2] or "",
                    "compression_implicated": bool(r[3]),
                    "trace_ids": list(r[4] or []),
                    "pruned_chunk_types": list(r[5] or []),
                    "timestamp": r[6].isoformat() if r[6] else "",
                }
                for r in rows
            ]

        return self._execute("failures load", op) or []
