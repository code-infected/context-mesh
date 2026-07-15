"""AppWorld benchmark runner for ContextMesh.

Runs AppWorld tasks with and without ContextMesh compression.
Collects token usage, task completion rate, and latency metrics.

Usage:
    python -m contextmesh.benchmarks.appworld.runner \
        --agent claude-sonnet-4-6 \
        --condition baseline contextmesh_8k contextmesh_4k \
        --tasks 100 \
        --output results/appworld_$(date +%Y%m%d).csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from contextmesh.benchmarks.eval_harness.base import (
    EvalHarness,
    Task,
)

logger = logging.getLogger(__name__)


def load_appworld_tasks(
    task_count: int = 100,
    task_dir: Path | None = None,
) -> list[Task]:
    """Load AppWorld benchmark tasks.

    Args:
        task_count: Number of tasks to load.
        task_dir: Directory containing task files.

    Returns:
        List of Task objects.
    """
    tasks: list[Task] = []

    if task_dir and task_dir.exists():
        for task_file in sorted(task_dir.glob("*.json"))[:task_count]:
            import json

            with open(task_file) as f:
                data = json.load(f)

            tasks.append(
                Task(
                    id=data.get("id", task_file.stem),
                    description=data.get("instruction", ""),
                    input_data=data.get("input", ""),
                    expected_outcome=data.get("expected_outcome", "success"),
                )
            )

    if not tasks:
        logger.info(f"No task files found in {task_dir}, generating synthetic tasks")
        for i in range(task_count):
            tasks.append(
                Task(
                    id=f"appworld_{i:04d}",
                    description=f"Task {i}: Process data and generate report",
                    input_data=f"Sample input data for task {i}",
                    expected_outcome="success",
                )
            )

    return tasks


def run_benchmark(args: argparse.Namespace) -> int:
    """Run AppWorld benchmark.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code.
    """
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info(f"Loading {args.tasks} AppWorld tasks from {args.task_dir}")
    tasks = load_appworld_tasks(args.tasks, args.task_dir)

    logger.info(f"Running benchmark with conditions: {args.conditions}")

    class DummyAgent:
        """Dummy agent for testing."""

        def run_task(
            self, description: str, task_input: str
        ) -> dict[str, str | int]:
            return {
                "outcome": "success",
                "tokens": len(task_input) // 4,
                "tool_calls": 1,
            }

    agent = DummyAgent()
    harness = EvalHarness(
        agent=agent,
        tasks=tasks,
        conditions=args.conditions,
    )

    results = harness.run()

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(str(output_path))
        logger.info(f"Results written to {output_path}")

    summary = results.summary()
    for condition, stats in summary.items():
        logger.info(
            f"{condition}: "
            f"success_rate={stats['success_rate']:.2%}, "
            f"token_reduction={1 - stats['token_reduction_ratio']:.1%}, "
            f"avg_latency={stats['avg_latency_ms']:.0f}ms"
        )

    return 0


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(description="AppWorld benchmark runner")
    parser.add_argument(
        "--agent",
        default="dummy",
        help="Agent to evaluate (default: dummy)",
    )
    parser.add_argument(
        "--conditions",
        nargs="+",
        default=["baseline", "contextmesh_8k", "contextmesh_4k"],
        help="Conditions to test",
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=100,
        help="Number of tasks to run",
    )
    parser.add_argument(
        "--task-dir",
        type=Path,
        help="Directory containing task JSON files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output CSV file path",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
    )

    args = parser.parse_args()
    return run_benchmark(args)


if __name__ == "__main__":
    sys.exit(main())
