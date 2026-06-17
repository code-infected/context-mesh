"""Task outcome webhook receiver.

Consumes task completion events from agents to detect
compression-related failures for the ACON loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskOutcome(Enum):
    """Task completion outcome."""

    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class TaskOutcomeEvent:
    """Task outcome event from agent webhook.

    Attributes:
        task_id: Unique task identifier.
        session_id: Session this task belongs to.
        outcome: Success or failure.
        failure_reason: Error message if failed.
        evaluation_score: Optional evaluation score.
        agent_final_output: Agent's final output text.
    """

    task_id: str
    session_id: str | None
    outcome: TaskOutcome
    failure_reason: str | None = None
    evaluation_score: float | None = None
    agent_final_output: str | None = None


class FailureDetector:
    """Detects when compression likely caused task failure.

    A task is considered compression-failed when:
    1. Outcome is "failed"
    2. Failure reason indicates missing context (ImportError, NameError, etc.)
    3. Re-running with full context succeeds

    Attributes:
        failure_patterns: Regex patterns that indicate compression-related failure.
    """

    def __init__(self) -> None:
        """Initialize failure detector."""
        import re

        self.failure_patterns = [
            re.compile(r"NameError", re.IGNORECASE),
            re.compile(r"ImportError", re.IGNORECASE),
            re.compile(r"AttributeError", re.IGNORECASE),
            re.compile(r"undefined is not", re.IGNORECASE),
            re.compile(r"cannot import", re.IGNORECASE),
            re.compile(r"has no attribute", re.IGNORECASE),
            re.compile(r"is not defined", re.IGNORECASE),
            re.compile(r"module not found", re.IGNORECASE),
        ]

    def is_compression_failure(
        self,
        outcome: TaskOutcome,
        failure_reason: str | None,
    ) -> bool:
        """Check if a failure is likely compression-related.

        Args:
            outcome: Task outcome.
            failure_reason: Error message if failed.

        Returns:
            True if failure pattern matches compression-related errors.
        """
        if outcome != TaskOutcome.FAILED:
            return False

        if not failure_reason:
            return False

        return any(
            pattern.search(failure_reason) for pattern in self.failure_patterns
        )

    def process_outcome(
        self, event: TaskOutcomeEvent
    ) -> bool:
        """Process a task outcome event.

        Args:
            event: Task outcome event.

        Returns:
            True if this is a compression-related failure.
        """
        return self.is_compression_failure(event.outcome, event.failure_reason)
