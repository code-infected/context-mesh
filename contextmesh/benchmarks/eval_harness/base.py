"""Generic evaluation harness for ContextMesh benchmarks.

Runs tasks under different conditions (baseline, contextmesh_8k, contextmesh_4k)
and collects metrics: token usage, task completion rate, latency.

Usage:
    harness = EvalHarness(
        agent=MyTestAgent(),
        tasks=load_tasks("appworld"),
        conditions=["baseline", "contextmesh_8k", "contextmesh_4k"]
    )
    results = harness.run()
    results.to_csv("results.csv")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from contextmesh.core.chunker.base import CompressionInput
from contextmesh.core.pipeline import CompressionPipeline

logger = logging.getLogger(__name__)


class Agent(Protocol):
    """Protocol for agents that can be evaluated."""

    def run_task(self, task_description: str, task_input: str) -> dict[str, Any]:
        """Run a task and return result with outcome and token usage."""
        ...


@dataclass
class TaskResult:
    """Result of running a single task.

    Attributes:
        task_id: Unique task identifier.
        condition: Condition under which task was run.
        outcome: "success" or "failed".
        original_tokens: Tokens before compression.
        compressed_tokens: Tokens after compression.
        latency_ms: Total latency in milliseconds.
        tool_calls: Number of tool calls made.
        error_message: Error message if failed.
    """

    task_id: str
    condition: str
    outcome: str
    original_tokens: int = 0
    compressed_tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: int = 0
    error_message: str | None = None


@dataclass
class Task:
    """A single benchmark task.

    Attributes:
        id: Unique task identifier.
        description: Task description for the agent.
        input_data: Input data for the task.
        expected_outcome: Expected outcome for validation.
    """

    id: str
    description: str
    input_data: str
    expected_outcome: str


@dataclass
class BenchmarkResults:
    """Aggregated benchmark results.

    Attributes:
        results: Individual task results.
        metadata: Benchmark metadata.
    """

    results: list[TaskResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_csv(self, path: str) -> None:
        """Export results to CSV.

        Args:
            path: Output file path.
        """
        import csv

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "task_id", "condition", "outcome",
                "original_tokens", "compressed_tokens",
                "latency_ms", "tool_calls", "error_message"
            ])
            writer.writeheader()
            for r in self.results:
                writer.writerow({
                    "task_id": r.task_id,
                    "condition": r.condition,
                    "outcome": r.outcome,
                    "original_tokens": r.original_tokens,
                    "compressed_tokens": r.compressed_tokens,
                    "latency_ms": r.latency_ms,
                    "tool_calls": r.tool_calls,
                    "error_message": r.error_message or "",
                })

    def summary(self) -> dict[str, dict[str, float]]:
        """Get summary statistics per condition.

        Returns:
            Dictionary of condition -> stats.
        """
        from collections import defaultdict

        by_condition: dict[str, list[TaskResult]] = defaultdict(list)
        for r in self.results:
            by_condition[r.condition].append(r)

        summary: dict[str, dict[str, float]] = {}
        for condition, results in by_condition.items():
            total_original = sum(r.original_tokens for r in results)
            total_compressed = sum(r.compressed_tokens for r in results)
            success_count = sum(1 for r in results if r.outcome == "success")
            total_latency = sum(r.latency_ms for r in results)

            summary[condition] = {
                "task_count": len(results),
                "success_rate": success_count / len(results) if results else 0.0,
                "avg_original_tokens": total_original / len(results) if results else 0.0,
                "avg_compressed_tokens": total_compressed / len(results) if results else 0.0,
                "token_reduction_ratio": total_compressed / total_original if total_original > 0 else 1.0,
                "avg_latency_ms": total_latency / len(results) if results else 0.0,
            }

        return summary


class EvalHarness:
    """Generic evaluation harness for ContextMesh.

    Runs tasks under different conditions and collects metrics.

    Attributes:
        agent: Agent to evaluate.
        tasks: List of tasks to run.
        conditions: Conditions to test.
        pipeline: Compression pipeline (used for non-baseline conditions).
    """

    def __init__(
        self,
        agent: Agent,
        tasks: list[Task],
        conditions: list[str] | None = None,
    ) -> None:
        """Initialize evaluation harness.

        Args:
            agent: Agent to evaluate.
            tasks: Tasks to run.
            conditions: Conditions to test (default: baseline + contextmesh variants).
        """
        self.agent = agent
        self.tasks = tasks
        self.conditions = conditions or ["baseline", "contextmesh_8k", "contextmesh_4k"]
        self.pipeline = CompressionPipeline()

    def run(self) -> BenchmarkResults:
        """Run all tasks under all conditions.

        Returns:
            Aggregated benchmark results.
        """
        results = BenchmarkResults(
            metadata={
                "conditions": self.conditions,
                "task_count": len(self.tasks),
            }
        )

        for task in self.tasks:
            for condition in self.conditions:
                result = self._run_task(task, condition)
                results.results.append(result)

        return results

    def _run_task(self, task: Task, condition: str) -> TaskResult:
        """Run a single task under a condition.

        Args:
            task: Task to run.
            condition: Condition name.

        Returns:
            Task result.
        """
        start_time = time.monotonic()

        try:
            if condition == "baseline":
                return self._run_baseline(task, start_time)
            elif condition.startswith("contextmesh"):
                budget = self._parse_budget(condition)
                return self._run_contextmesh(task, budget, start_time)
            else:
                return TaskResult(
                    task_id=task.id,
                    condition=condition,
                    outcome="failed",
                    error_message=f"Unknown condition: {condition}",
                )
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                condition=condition,
                outcome="failed",
                latency_ms=(time.monotonic() - start_time) * 1000,
                error_message=str(e),
            )

    def _run_baseline(self, task: Task, start_time: float) -> TaskResult:
        """Run task without compression (baseline).

        Args:
            task: Task to run.
            start_time: Start time for latency calculation.

        Returns:
            Task result.
        """
        result = self.agent.run_task(task.description, task.input_data)
        latency_ms = (time.monotonic() - start_time) * 1000

        return TaskResult(
            task_id=task.id,
            condition="baseline",
            outcome=result.get("outcome", "success"),
            original_tokens=result.get("tokens", 0),
            compressed_tokens=result.get("tokens", 0),
            latency_ms=latency_ms,
            tool_calls=result.get("tool_calls", 0),
            error_message=result.get("error"),
        )

    def _run_contextmesh(
        self, task: Task, budget: int, start_time: float
    ) -> TaskResult:
        """Run task with ContextMesh compression.

        Args:
            task: Task to run.
            budget: Token budget.
            start_time: Start time for latency calculation.

        Returns:
            Task result.
        """
        inp = CompressionInput(
            session_id="eval-session",
            task_id=task.id,
            tool_name="eval_tool",
            tool_args={},
            raw_output=task.input_data,
            task_description=task.description,
            budget_tokens=budget,
        )

        compressed = self.pipeline.compress(inp)
        latency_ms = (time.monotonic() - start_time) * 1000

        result = self.agent.run_task(task.description, compressed.compressed_output)

        return TaskResult(
            task_id=task.id,
            condition=f"contextmesh_{budget}k",
            outcome=result.get("outcome", "success"),
            original_tokens=compressed.original_tokens,
            compressed_tokens=compressed.compressed_tokens,
            latency_ms=latency_ms,
            tool_calls=result.get("tool_calls", 0),
            error_message=result.get("error"),
        )

    def _parse_budget(self, condition: str) -> int:
        """Parse budget from condition name.

        Args:
            condition: Condition name (e.g., "contextmesh_8k").

        Returns:
            Budget in tokens.
        """
        parts = condition.split("_")
        if len(parts) == 2:
            try:
                return int(parts[1].replace("k", "000"))
            except ValueError:
                pass
        return 8000
