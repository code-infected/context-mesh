"""ACON guideline engine implementation.

Analyzes compression failures and updates extraction guidelines
to prevent the same pattern from recurring.

Algorithm (from ACON paper, Section 4.2/4.3):
    1. For each failed task, retrieve compression traces
    2. Re-run agent with full context (no compression)
    3. If re-run succeeds, failure was compression-related
    4. Diff pruned chunks against what the full-context run accessed
    5. After min_failures_before_update failures implicate the same
       (tool, chunk_type), update its guideline multiplier
    6. Multiplier update: new = old * increment, capped at max
    7. Multipliers decay back toward 1.0 after decay_days without
       new failures (overcorrection guard)

The re-run in step 2 is the only agent/LLM call in the loop and is
pluggable: pass a rerun_agent callback. Without one, the engine falls
back to pattern-based implication (the failure detector's regex
match), which is weaker but requires no agent access.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from contextmesh.core.scorer.guideline_adjuster import GuidelineStore

if TYPE_CHECKING:
    from contextmesh.feedback.trace_store import CompressionTrace

logger = logging.getLogger(__name__)


@dataclass
class ACONConfig:
    """Configuration for ACON guideline engine.

    Attributes:
        max_multiplier: Maximum score multiplier cap.
        multiplier_increment: Multiplicative increase per update.
        min_failures_before_update: Failures implicating a
            (tool, chunk_type) before its guideline is updated.
        decay_days: Days without failures before decay applies.
        decay_factor: Multiplier shrink factor when decaying.
    """

    max_multiplier: float = 3.0
    multiplier_increment: float = 1.2
    min_failures_before_update: int = 3
    decay_days: int = 30
    decay_factor: float = 0.95


@dataclass
class RerunResult:
    """Outcome of re-running a failed task with full context.

    Attributes:
        success: Whether the full-context run completed the task.
        accessed_content: Content the successful run relied on;
            used to identify which pruned chunks were root causes.
    """

    success: bool
    accessed_content: str = ""


@dataclass
class GuidelineUpdate:
    """One guideline change, for the audit trail."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    chunk_type: str = ""
    old_multiplier: float = 1.0
    new_multiplier: float = 1.0
    task_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "chunk_type": self.chunk_type,
            "old_multiplier": self.old_multiplier,
            "new_multiplier": self.new_multiplier,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
        }


@dataclass
class FailureAnalysis:
    """Result of analyzing one failed task."""

    task_id: str
    compression_implicated: bool
    root_cause_chunk_ids: list[str] = field(default_factory=list)
    root_cause_chunk_types: list[str] = field(default_factory=list)
    updates: list[GuidelineUpdate] = field(default_factory=list)


class ACONGuidelineEngine:
    """ACON failure-driven guideline optimization.

    Implements the ACON paper's gradient-free optimization approach
    for improving compression guidelines based on task failures.

    Attributes:
        config: ACON configuration.
        guideline_store: Store for extraction guidelines (share this
            instance with the pipeline's GuidelineAdjuster so updates
            take effect on subsequent compressions).
        history: Audit trail of guideline updates.
    """

    def __init__(
        self,
        guideline_store: GuidelineStore | None = None,
        config: ACONConfig | None = None,
        persistence: object | None = None,
    ) -> None:
        """Initialize ACON guideline engine.

        Args:
            guideline_store: Optional guideline store.
            config: Optional ACON configuration.
            persistence: Optional GuidelinePersistence; when set,
                guidelines are loaded from the database at startup and
                every update is written back (fail-open).
        """
        self.config = config or ACONConfig()
        self.guideline_store = guideline_store or GuidelineStore(
            max_multiplier=self.config.max_multiplier
        )
        self.persistence = persistence
        self.history: list[GuidelineUpdate] = []
        # Failures implicating each (tool, chunk_type), gating updates.
        self._failure_counts: dict[tuple[str, str], int] = {}
        self._last_failure_at: dict[tuple[str, str], datetime] = {}

        if self.persistence is not None:
            try:
                records = self.persistence.load_guidelines()
                if records:
                    self.guideline_store.load_records(records)
                    logger.info("Loaded %d persisted guidelines", len(records))
            except Exception:
                logger.exception("Failed to load persisted guidelines")

    def analyze_task_failure(
        self,
        task_id: str,
        traces: list[CompressionTrace],
        chunk_contents: dict[str, str] | None = None,
        rerun_agent: Callable[[str], RerunResult] | None = None,
    ) -> FailureAnalysis:
        """Run the ACON analysis for one failed task.

        Args:
            task_id: Failed task identifier.
            traces: Compression traces recorded for the task.
            chunk_contents: Optional map of pruned chunk ID -> content,
                used to diff against the full-context run.
            rerun_agent: Optional callback that re-runs the task with
                full (uncompressed) context and reports the result.

        Returns:
            FailureAnalysis with root causes and any guideline updates.
        """
        if not traces:
            return FailureAnalysis(task_id=task_id, compression_implicated=False)

        pruned_by_trace: list[tuple[str, list[str], list[str]]] = [
            (t.tool_name, t.chunk_ids_pruned, t.chunk_types_pruned) for t in traces
        ]

        rerun: RerunResult | None = None
        if rerun_agent is not None:
            try:
                rerun = rerun_agent(task_id)
            except Exception as e:
                logger.warning("Full-context re-run for %s failed to execute: %s", task_id, e)

            if rerun is not None and not rerun.success:
                # Full context also fails: the failure is not
                # compression's fault. No guideline update.
                logger.info(
                    "Task %s fails with full context too; compression not implicated",
                    task_id,
                )
                return FailureAnalysis(task_id=task_id, compression_implicated=False)

        analysis = FailureAnalysis(task_id=task_id, compression_implicated=True)

        # A failed task counts as ONE failure per (tool, chunk_type), no
        # matter how many chunks of that type were pruned — otherwise a
        # single incident saturates the multiplier cap by itself.
        implicated_pairs: set[tuple[str, str]] = set()

        for tool_name, pruned_ids, pruned_types in pruned_by_trace:
            for chunk_id, chunk_type in zip(pruned_ids, pruned_types, strict=False):
                if not self._is_root_cause(chunk_id, chunk_contents, rerun):
                    continue
                analysis.root_cause_chunk_ids.append(chunk_id)
                analysis.root_cause_chunk_types.append(chunk_type)
                implicated_pairs.add((tool_name, chunk_type))

        for tool_name, chunk_type in sorted(implicated_pairs):
            update = self._register_failure(tool_name, chunk_type, task_id)
            if update is not None:
                analysis.updates.append(update)

        return analysis

    def _is_root_cause(
        self,
        chunk_id: str,
        chunk_contents: dict[str, str] | None,
        rerun: RerunResult | None,
    ) -> bool:
        """Decide whether a pruned chunk contributed to the failure.

        With a successful full-context re-run and known chunk contents,
        a pruned chunk is a root cause iff the re-run's accessed content
        overlaps it (ACON's paired-trajectory diff). Without that
        evidence, every pruned chunk is treated as a candidate.
        """
        if rerun is None or not rerun.accessed_content or not chunk_contents:
            return True
        content = chunk_contents.get(chunk_id)
        if not content:
            return True
        probe = content.strip()[:200]
        return bool(probe) and probe in rerun.accessed_content

    def _register_failure(
        self, tool_name: str, chunk_type: str, task_id: str
    ) -> GuidelineUpdate | None:
        """Count a failure and update the guideline once the gate clears.

        Args:
            tool_name: Tool whose output was compressed.
            chunk_type: Type of the pruned root-cause chunk.
            task_id: Failed task, recorded as evidence.

        Returns:
            The guideline update if one was applied, else None.
        """
        key = (tool_name, chunk_type)
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
        self._last_failure_at[key] = datetime.now(UTC)
        self.guideline_store.add_evidence(tool_name, chunk_type, task_id)

        if self._failure_counts[key] < self.config.min_failures_before_update:
            return None

        # Gate cleared: apply the multiplicative update and reset the
        # counter so the next update needs fresh failures.
        self._failure_counts[key] = 0

        old = self.guideline_store.get_multiplier(tool_name, chunk_type)
        new = min(
            old * self.config.multiplier_increment,
            self.config.max_multiplier,
        )
        if new == old:
            return None

        self.guideline_store.set_guideline(tool_name, chunk_type, new)
        self.guideline_store.increment_update_count(tool_name, chunk_type)

        update = GuidelineUpdate(
            tool_name=tool_name,
            chunk_type=chunk_type,
            old_multiplier=old,
            new_multiplier=new,
            task_id=task_id,
        )
        self.history.append(update)
        logger.info(
            "Updated guideline %s:%s from %.2f to %.2f (task %s)",
            tool_name, chunk_type, old, new, task_id,
        )

        if self.persistence is not None:
            try:
                self.persistence.upsert_guideline(
                    {
                        "tool_name": tool_name,
                        "chunk_type": chunk_type,
                        "score_multiplier": new,
                        "update_count": self.guideline_store.get_update_count(
                            tool_name, chunk_type
                        ),
                        "evidence_task_ids": self.guideline_store.get_evidence(
                            tool_name, chunk_type
                        ),
                    }
                )
                self.persistence.record_history(update.to_dict())
            except Exception:
                logger.exception("Failed to persist guideline update")

        return update

    def analyze_failure(
        self,
        task_id: str,
        tool_name: str,
        pruned_chunk_ids: list[str],
        pruned_chunk_types: list[str],
    ) -> dict[str, float]:
        """Analyze a failure from raw pruned-chunk lists.

        Compatibility wrapper over analyze_task_failure for callers
        that don't hold CompressionTrace objects.

        Args:
            task_id: Failed task identifier.
            tool_name: Tool that produced the pruned chunks.
            pruned_chunk_ids: IDs of chunks that were pruned.
            pruned_chunk_types: Types of pruned chunks.

        Returns:
            Dictionary of "tool:chunk_type" -> new multiplier for
            guidelines that were updated.
        """
        from contextmesh.feedback.trace_store import CompressionTrace

        trace = CompressionTrace(
            task_id=task_id,
            tool_name=tool_name,
            chunk_ids_pruned=list(pruned_chunk_ids),
            chunk_types_pruned=list(pruned_chunk_types),
        )
        analysis = self.analyze_task_failure(task_id, [trace])
        return {
            f"{u.tool_name}:{u.chunk_type}": u.new_multiplier
            for u in analysis.updates
        }

    def apply_decay(self, now: datetime | None = None) -> int:
        """Decay multipliers for guidelines without recent failures.

        Args:
            now: Override current time (for tests).

        Returns:
            Number of guidelines decayed.
        """
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=self.config.decay_days)
        decayed = 0

        for key, multiplier in list(self.guideline_store.guidelines.items()):
            if multiplier <= 1.0:
                continue
            last_failure = self._last_failure_at.get(key)
            if last_failure is not None and last_failure > cutoff:
                continue
            new_val = max(1.0, multiplier * self.config.decay_factor)
            self.guideline_store.guidelines[key] = new_val
            decayed += 1

        return decayed

    def get_history(self) -> list[dict]:
        """Guideline update history as records, newest first."""
        return [u.to_dict() for u in reversed(self.history)]
