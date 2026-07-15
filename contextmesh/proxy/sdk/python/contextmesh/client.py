"""ContextMesh Python SDK.

Provides a simple interface for integrating ContextMesh compression
into custom agent loops. Runs the compression pipeline in-process
(no server required) with tracing and the ACON feedback loop wired
up, so report_outcome() actually updates extraction guidelines.

Usage:
    from contextmesh import ContextMesh

    cm = ContextMesh(task_description="fix auth bug", budget_tokens=8000)
    raw_result = file_tool.read("/src/auth.py")
    compressed = cm.compress(output=raw_result, tool_name="read_file",
                             tool_args={"path": "/src/auth.py"})
    # ... task finishes ...
    cm.report_outcome(compressed.task_id, success=False,
                      failure_reason="ImportError: cannot import verify_token")
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from contextmesh.proxy.sdk.python.contextmesh.models import (
    CompressionMetadata,
    CompressionResult,
)

logger = logging.getLogger(__name__)


class ContextMesh:
    """Python SDK for ContextMesh compression.

    Wraps the core compression pipeline for custom agent loops that
    don't use MCP. All compression calls are traced; reporting a task
    failure runs the ACON analysis, and learned guidelines apply to
    subsequent compress() calls on this instance.

    Attributes:
        task_description: The current task description for scoring.
        budget_tokens: Default token budget per compression.
        session_id: Session identifier used in traces.

    Example:
        >>> cm = ContextMesh(task_description="refactor auth", budget_tokens=8000)
        >>> result = cm.compress(output=file_content, tool_name="read_file")
        >>> print(result.metadata.compression_ratio)
    """

    def __init__(
        self,
        task_description: str,
        budget_tokens: int = 8000,
        config_path: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Initialize ContextMesh SDK.

        Args:
            task_description: Current task description.
            budget_tokens: Token budget per call.
            config_path: Optional config.yaml path.
            session_id: Session identifier; generated when omitted.
        """
        from contextmesh.config import create_pipeline, load_config
        from contextmesh.core.scorer.guideline_adjuster import GuidelineAdjuster
        from contextmesh.feedback.failure_detector import FailureDetector
        from contextmesh.feedback.guideline_engine import ACONGuidelineEngine
        from contextmesh.feedback.trace_store import TraceStore

        self.task_description = task_description
        self.budget_tokens = budget_tokens
        self.session_id = session_id or f"sdk-{uuid.uuid4().hex[:8]}"
        self._recent_steps: list[str] = []
        self._task_counter = 0

        self._config = load_config(config_path)
        self._trace_store = TraceStore(database_url=self._config.database_url)
        self._failure_detector = FailureDetector()
        self._guideline_engine = ACONGuidelineEngine()

        self._pipeline = create_pipeline(self._config)
        self._pipeline.trace_store = self._trace_store
        self._pipeline.guideline_adjuster = GuidelineAdjuster(
            store=self._guideline_engine.guideline_store
        )

    def set_task_description(self, task_description: str) -> None:
        """Update the task description as the agent's focus evolves."""
        self.task_description = task_description

    def add_step(self, step: str) -> None:
        """Record an agent reasoning step (last 3 are used for scoring)."""
        self._recent_steps.append(step)
        if len(self._recent_steps) > 3:
            self._recent_steps.pop(0)

    def compress(
        self,
        output: str,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
        task_id: str | None = None,
    ) -> CompressionResult:
        """Compress tool output.

        Never raises on compression errors: the raw output is returned
        with metadata.compression_failed set (fail-open).

        Args:
            output: Raw tool output text.
            tool_name: Name of the tool that produced the output.
            tool_args: Arguments passed to the tool.
            budget_tokens: Override default budget for this call.
            task_id: Task identifier; generated when omitted.

        Returns:
            CompressionResult with compressed content and metadata.
        """
        from contextmesh.core.chunker.base import CompressionInput

        if task_id is None:
            task_id = f"task-{self.session_id}-{self._task_counter}"
            self._task_counter += 1

        inp = CompressionInput(
            session_id=self.session_id,
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args or {},
            raw_output=output,
            task_description=self.task_description,
            recent_steps=list(self._recent_steps),
            budget_tokens=budget_tokens or self.budget_tokens,
        )

        try:
            result = self._pipeline.compress(inp)
        except Exception:
            logger.exception("Compression failed; returning raw output")
            return CompressionResult(
                content=output,
                metadata=CompressionMetadata(
                    original_tokens=0,
                    compressed_tokens=0,
                    compression_ratio=1.0,
                    chunks_selected=0,
                    chunks_total=0,
                    compression_failed=True,
                ),
                task_id=task_id,
            )

        return CompressionResult(
            content=result.compressed_output,
            metadata=CompressionMetadata(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                compression_ratio=result.compression_ratio,
                chunks_selected=result.chunks_selected,
                chunks_total=result.chunks_total,
                trace_id=result.trace_id,
            ),
            task_id=task_id,
        )

    def report_outcome(
        self,
        task_id: str,
        success: bool | None = None,
        outcome: str | None = None,
        failure_reason: str | None = None,
    ) -> bool:
        """Report a task outcome for the ACON feedback loop.

        Failed tasks whose failure pattern implicates compression
        trigger guideline analysis; learned score multipliers apply to
        subsequent compress() calls on this instance.

        Args:
            task_id: Task identifier (from CompressionResult.task_id).
            success: Convenience boolean form of outcome.
            outcome: "success", "failed", or "unknown".
            failure_reason: Error message if failed.

        Returns:
            True if compression was implicated in a failure.
        """
        from contextmesh.feedback.failure_detector import (
            TaskOutcome,
            TaskOutcomeEvent,
        )

        if outcome is None:
            outcome = "unknown" if success is None else ("success" if success else "failed")
        try:
            parsed_outcome = TaskOutcome(outcome)
        except ValueError:
            parsed_outcome = TaskOutcome.UNKNOWN

        event = TaskOutcomeEvent(
            task_id=task_id,
            session_id=self.session_id,
            outcome=parsed_outcome,
            failure_reason=failure_reason,
        )

        if not self._failure_detector.process_outcome(event):
            logger.info("Task outcome recorded: %s -> %s", task_id, outcome)
            return False

        traces = self._trace_store.get_traces_for_task(task_id)
        if not traces:
            logger.info("No compression traces for failed task %s", task_id)
            return False

        analysis = self._guideline_engine.analyze_task_failure(task_id, traces)
        if analysis.updates:
            logger.info(
                "ACON updated %d guideline(s) after task %s",
                len(analysis.updates), task_id,
            )
        return analysis.compression_implicated

    @property
    def guidelines(self) -> list[dict[str, Any]]:
        """Current learned extraction guidelines."""
        return self._guideline_engine.guideline_store.to_records()
