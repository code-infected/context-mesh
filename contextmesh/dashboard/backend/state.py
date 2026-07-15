"""Shared application state for the dashboard backend.

One pipeline, one trace store, one guideline store — shared across
requests. The guideline store is shared between the pipeline's
adjuster and the ACON engine, so guideline updates take effect on
subsequent compressions without a restart.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from contextmesh.config import Config, load_config
from contextmesh.core.pipeline import CompressionPipeline
from contextmesh.core.scorer.guideline_adjuster import GuidelineAdjuster, GuidelineStore
from contextmesh.feedback.failure_detector import (
    FailureDetector,
    TaskOutcome,
    TaskOutcomeEvent,
)
from contextmesh.feedback.guideline_engine import ACONConfig, ACONGuidelineEngine
from contextmesh.feedback.trace_store import TraceStore

logger = logging.getLogger(__name__)


@dataclass
class FailureRecord:
    """One analyzed task failure, for the dashboard."""

    task_id: str
    session_id: str
    failure_reason: str
    compression_implicated: bool
    pruned_chunk_types: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "failure_reason": self.failure_reason,
            "compression_implicated": self.compression_implicated,
            "timestamp": self.timestamp,
            "pruned_chunk_types": self.pruned_chunk_types,
            "trace_ids": self.trace_ids,
        }


class AppState:
    """Container for the backend's long-lived components."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()

        self.trace_store = TraceStore(
            database_url=self.config.database_url,
            batch_size=int(self.config.get("feedback", "trace_batch_size", default=100)),
        )

        self.persistence = None
        if self.config.database_url:
            from contextmesh.feedback.guideline_persistence import GuidelinePersistence

            persistence = GuidelinePersistence(self.config.database_url)
            if persistence.available:
                self.persistence = persistence

        self.guideline_store = GuidelineStore(
            max_multiplier=float(self.config.get("feedback", "max_multiplier", default=3.0))
        )
        self.guideline_engine = ACONGuidelineEngine(
            guideline_store=self.guideline_store,
            config=ACONConfig(
                max_multiplier=float(
                    self.config.get("feedback", "max_multiplier", default=3.0)
                ),
                min_failures_before_update=int(
                    self.config.get("feedback", "min_failures_before_update", default=3)
                ),
                decay_days=int(
                    self.config.get("feedback", "multiplier_decay_days", default=30)
                ),
            ),
            persistence=self.persistence,
        )
        self.failure_detector = FailureDetector()

        from contextmesh.config import create_pipeline

        self.pipeline: CompressionPipeline = create_pipeline(self.config)
        self.pipeline.guideline_adjuster = GuidelineAdjuster(
            store=self.guideline_store, persistence=self.persistence
        )
        self.pipeline.trace_store = self.trace_store

        self.failures: list[FailureRecord] = []
        self._lock = threading.Lock()

    def record_outcome(self, event: TaskOutcomeEvent) -> FailureRecord | None:
        """Process a task outcome; run ACON analysis on failure.

        Args:
            event: The reported outcome.

        Returns:
            The failure record if the task failed, else None.
        """
        if event.outcome != TaskOutcome.FAILED:
            return None

        traces = self.trace_store.get_traces_for_task(event.task_id)
        implicated = self.failure_detector.process_outcome(event)

        analysis = None
        if implicated and traces:
            analysis = self.guideline_engine.analyze_task_failure(
                event.task_id, traces
            )

        record = FailureRecord(
            task_id=event.task_id,
            session_id=event.session_id or "",
            failure_reason=event.failure_reason or "",
            compression_implicated=bool(implicated and traces),
            pruned_chunk_types=sorted(
                {ct for t in traces for ct in t.chunk_types_pruned}
            ),
            trace_ids=[t.id for t in traces],
            tool_names=sorted({t.tool_name for t in traces}),
        )
        if analysis is not None and analysis.updates:
            logger.info(
                "ACON updated %d guideline(s) after task %s",
                len(analysis.updates), event.task_id,
            )

        if self.persistence is not None:
            self.persistence.record_outcome(
                {
                    "task_id": event.task_id,
                    "session_id": event.session_id,
                    "outcome": event.outcome.value,
                    "failure_reason": event.failure_reason,
                    "evaluation_score": event.evaluation_score,
                    "agent_final_output": event.agent_final_output,
                }
            )
            self.persistence.record_failed_task(record.to_dict())

        with self._lock:
            self.failures.append(record)
        return record

    def overview(self) -> dict[str, Any]:
        """Aggregate KPIs for the dashboard in one call.

        The frontend previously derived these by fanning out one
        traces request per session; this endpoint replaces that.
        """
        traces = self.trace_store.get_all_traces()
        original = sum(t.original_token_count for t in traces)
        compressed = sum(t.compressed_token_count for t in traces)
        failures = self.all_failures()

        return {
            "sessions": len({t.session_id for t in traces}),
            "traces": len(traces),
            "tasks": len({t.task_id for t in traces}),
            "original_tokens": original,
            "compressed_tokens": compressed,
            "tokens_saved": original - compressed,
            "avg_compression_ratio": compressed / original if original else 1.0,
            "low_signal_traces": sum(1 for t in traces if t.low_signal),
            "failures": len(failures),
            "compression_implicated_failures": sum(
                1 for f in failures if f.get("compression_implicated")
            ),
            "guidelines_active": sum(
                1
                for g in self.guideline_store.to_records()
                if g["score_multiplier"] > 1.0
            ),
            "scorer_fallback": bool(self.pipeline.scorer.using_fallback),
            "trace_backend": self.trace_store.backend_name,
        }

    def start_decay_scheduler(self, interval_s: float = 86400.0) -> None:
        """Start the periodic guideline-decay job (idempotent).

        ACON multipliers decay back toward 1.0 for guidelines with no
        recent failures (overcorrection guard, spec failure mode 4);
        this runs that check on a daemon thread.

        Args:
            interval_s: Seconds between decay checks (default daily).
        """
        if getattr(self, "_decay_stop", None) is not None:
            return
        self._decay_stop = threading.Event()

        def loop() -> None:
            while not self._decay_stop.wait(interval_s):
                try:
                    decayed = self.guideline_engine.apply_decay()
                    if decayed:
                        logger.info("Guideline decay applied to %d guideline(s)", decayed)
                except Exception:
                    logger.exception("Guideline decay run failed")

        self._decay_thread = threading.Thread(
            target=loop, name="cm-guideline-decay", daemon=True
        )
        self._decay_thread.start()

    def stop_decay_scheduler(self) -> None:
        """Stop the decay job if running."""
        stop = getattr(self, "_decay_stop", None)
        if stop is not None:
            stop.set()
            self._decay_stop = None

    def all_failures(self) -> list[dict[str, Any]]:
        """Failure records, newest first, merged with persisted history."""
        with self._lock:
            records = [f.to_dict() for f in reversed(self.failures)]
        if self.persistence is not None:
            seen = {r["task_id"] for r in records}
            for persisted in self.persistence.load_failures():
                if persisted["task_id"] not in seen:
                    records.append(persisted)
        return records

    def get_trace_diff(self, trace_id: str) -> dict[str, Any] | None:
        """Build the chunk-level diff for one trace.

        Returns:
            Diff payload, or None when the trace is unknown or has no
            stored previews (older traces / oversized chunk sets).
        """
        trace = next(
            (t for t in self.trace_store.get_all_traces() if t.id == trace_id), None
        )
        if trace is None:
            return None

        previews = trace.metadata.get("chunk_previews") or {}
        if not previews:
            return None
        scores = trace.metadata.get("chunk_scores") or {}
        token_counts = trace.metadata.get("chunk_token_counts") or {}

        chunks = []
        for ids, types, selected in (
            (trace.chunk_ids_selected, trace.chunk_types_selected, True),
            (trace.chunk_ids_pruned, trace.chunk_types_pruned, False),
        ):
            for chunk_id, chunk_type in zip(ids, types, strict=True):
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "chunk_type": chunk_type,
                        "token_count": int(token_counts.get(chunk_id, 0)),
                        "selected": selected,
                        "score": scores.get(chunk_id),
                        "preview": previews.get(chunk_id, ""),
                    }
                )

        return {
            "trace_id": trace.id,
            "session_id": trace.session_id,
            "task_id": trace.task_id,
            "tool_name": trace.tool_name,
            "timestamp": trace.created_at,
            "original_tokens": trace.original_token_count,
            "compressed_tokens": trace.compressed_token_count,
            "compression_ratio": trace.compression_ratio,
            "chunks": chunks,
        }

    def failure_counts_by_tool(self) -> dict[str, int]:
        """Number of compression-implicated failures per tool."""
        counts: dict[str, int] = {}
        with self._lock:
            for record in self.failures:
                if not record.compression_implicated:
                    continue
                for tool in record.tool_names:
                    counts[tool] = counts.get(tool, 0) + 1
        return counts


_state: AppState | None = None


def get_state() -> AppState:
    """Get (lazily creating) the process-wide AppState."""
    global _state
    if _state is None:
        _state = AppState()
    return _state


def reset_state() -> None:
    """Drop the AppState singleton (for tests)."""
    global _state
    _state = None
