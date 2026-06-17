"""Benchmark metrics collection and reporting.

Collects and reports benchmark metrics including:
- Token reduction ratio
- Task completion rate
- Latency overhead
- Compression ratio distribution
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricStats:
    """Statistical summary of a metric.

    Attributes:
        count: Number of observations.
        mean: Average value.
        median: Median value.
        std_dev: Standard deviation.
        min_val: Minimum value.
        max_val: Maximum value.
        p50: 50th percentile.
        p90: 90th percentile.
        p95: 95th percentile.
        p99: 99th percentile.
    """

    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    std_dev: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    p50: float = 0.0
    p90: float = 0.0
    p95: float = 0.0
    p99: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "count": self.count,
            "mean": self.mean,
            "median": self.median,
            "std_dev": self.std_dev,
            "min": self.min_val,
            "max": self.max_val,
            "p50": self.p50,
            "p90": self.p90,
            "p95": self.p95,
            "p99": self.p99,
        }


def compute_stats(values: list[float]) -> MetricStats:
    """Compute statistics for a list of values.

    Args:
        values: List of numeric values.

    Returns:
        MetricStats with computed statistics.
    """
    if not values:
        return MetricStats()

    sorted_values = sorted(values)
    count = len(sorted_values)

    def percentile(p: float) -> float:
        """Compute percentile from sorted values.

        Args:
            p: Percentile (0-100).

        Returns:
            Percentile value.
        """
        idx = int(count * p / 100)
        idx = min(idx, count - 1)
        return sorted_values[idx]

    return MetricStats(
        count=count,
        mean=statistics.mean(values),
        median=statistics.median(values),
        std_dev=statistics.stdev(values) if count > 1 else 0.0,
        min_val=min(values),
        max_val=max(values),
        p50=percentile(50),
        p90=percentile(90),
        p95=percentile(95),
        p99=percentile(99),
    )


@dataclass
class BenchmarkMetrics:
    """Collected benchmark metrics.

    Attributes:
        token_reduction_ratios: List of compression ratios.
        latencies_ms: List of latency measurements.
        task_outcomes: List of task outcomes ("success" or "failed").
        tool_call_counts: List of tool call counts per task.
        chunk_selection_stats: Stats about chunk selection.
    """

    token_reduction_ratios: list[float] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    task_outcomes: list[str] = field(default_factory=list)
    tool_call_counts: list[int] = field(default_factory=list)
    chunk_selection_stats: list[dict[str, int]] = field(default_factory=list)

    def add_result(
        self,
        compression_ratio: float,
        latency_ms: float,
        outcome: str,
        tool_calls: int = 0,
        chunks_selected: int = 0,
        chunks_total: int = 0,
    ) -> None:
        """Add a benchmark result.

        Args:
            compression_ratio: Ratio of compressed to original tokens.
            latency_ms: Latency in milliseconds.
            outcome: Task outcome ("success" or "failed").
            tool_calls: Number of tool calls made.
            chunks_selected: Number of chunks selected.
            chunks_total: Total chunks available.
        """
        self.token_reduction_ratios.append(compression_ratio)
        self.latencies_ms.append(latency_ms)
        self.task_outcomes.append(outcome)
        self.tool_call_counts.append(tool_calls)

        if chunks_total > 0:
            self.chunk_selection_stats.append({
                "selected": chunks_selected,
                "total": chunks_total,
            })

    def summary(self) -> dict[str, Any]:
        """Get summary of all metrics.

        Returns:
            Dictionary with metric summaries.
        """
        success_count = sum(1 for o in self.task_outcomes if o == "success")
        total_count = len(self.task_outcomes)

        return {
            "token_reduction": compute_stats(self.token_reduction_ratios).to_dict(),
            "latency_ms": compute_stats(self.latencies_ms).to_dict(),
            "task_completion": {
                "total": total_count,
                "success": success_count,
                "failed": total_count - success_count,
                "success_rate": success_count / total_count if total_count > 0 else 0.0,
            },
            "tool_calls": compute_stats(
                [float(c) for c in self.tool_call_counts]
            ).to_dict(),
            "chunk_selection": {
                "avg_selected": statistics.mean(
                    [s["selected"] for s in self.chunk_selection_stats]
                ) if self.chunk_selection_stats else 0,
                "avg_total": statistics.mean(
                    [s["total"] for s in self.chunk_selection_stats]
                ) if self.chunk_selection_stats else 0,
            },
        }

    def report(self) -> str:
        """Generate a human-readable report.

        Returns:
            Formatted report string.
        """
        s = self.summary()

        lines = [
            "=" * 60,
            "ContextMesh Benchmark Report",
            "=" * 60,
            "",
            f"Tasks: {s['task_completion']['total']} "
            f"(success: {s['task_completion']['success']}, "
            f"failed: {s['task_completion']['failed']})",
            f"Success Rate: {s['task_completion']['success_rate']:.1%}",
            "",
            "Token Reduction (lower is better):",
            f"  Mean: {s['token_reduction']['mean']:.3f} "
            f"({(1 - s['token_reduction']['mean']) * 100:.1f}% reduction)",
            f"  Median: {s['token_reduction']['median']:.3f}",
            f"  P50: {s['token_reduction']['p50']:.3f}",
            f"  P99: {s['token_reduction']['p99']:.3f}",
            "",
            "Latency (ms):",
            f"  Mean: {s['latency_ms']['mean']:.1f}ms",
            f"  Median: {s['latency_ms']['median']:.1f}ms",
            f"  P50: {s['latency_ms']['p50']:.1f}ms",
            f"  P99: {s['latency_ms']['p99']:.1f}ms",
            "",
            "Chunk Selection:",
            f"  Avg Selected: {s['chunk_selection']['avg_selected']:.1f}",
            f"  Avg Total: {s['chunk_selection']['avg_total']:.1f}",
            "=" * 60,
        ]

        return "\n".join(lines)
