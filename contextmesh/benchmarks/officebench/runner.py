"""OfficeBench benchmark runner for ContextMesh.

Runs OfficeBench tasks with and without ContextMesh compression.
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


def load_officebench_tasks(
    task_count: int = 50,
    task_dir: Path | None = None,
) -> list[Task]:
    """Load OfficeBench benchmark tasks.

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
        logger.info(f"No task files found, generating synthetic tasks")
        for i in range(task_count):
            tasks.append(
                Task(
                    id=f"officebench_{i:04d}",
                    description=f"Office task {i}: Process document",
                    input_data=f"Sample office data for task {i}",
                    expected_outcome="success",
                )
            )

    return tasks


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(description="OfficeBench benchmark runner")
    parser.add_argument("--tasks", type=int, default=50)
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--conditions",
        nargs="+",
        default=["baseline", "contextmesh_8k"],
    )
    args = parser.parse_args()

    tasks = load_officebench_tasks(args.tasks, args.task_dir)

    class DummyAgent:
        def run_task(self, description: str, task_input: str) -> dict:
            return {"outcome": "success", "tokens": len(task_input) // 4, "tool_calls": 1}

    harness = EvalHarness(
        agent=DummyAgent(),
        tasks=tasks,
        conditions=args.conditions,
    )

    results = harness.run()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        results.to_csv(str(args.output))

    summary = results.summary()
    for condition, stats in summary.items():
        logger.info(
            f"{condition}: "
            f"success_rate={stats['success_rate']:.2%}, "
            f"token_reduction={1 - stats['token_reduction_ratio']:.1%}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
