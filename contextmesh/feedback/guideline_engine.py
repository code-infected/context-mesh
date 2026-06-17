"""ACON guideline engine implementation.

Analyzes compression failures and updates extraction guidelines
to prevent the same pattern from recurring.

Algorithm (from ACON paper):
    1. For each failed task, retrieve compression traces
    2. Re-run agent with full context (no compression)
    3. If re-run succeeds, failure was compression-related
    4. Diff pruned chunks against full-context access
    5. Update guideline multipliers for (tool, chunk_type) pairs
    6. Multiplier update: new = old * 1.2, capped at 3.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from contextmesh.core.scorer.guideline_adjuster import GuidelineStore

logger = logging.getLogger(__name__)


@dataclass
class ACONConfig:
    """Configuration for ACON guideline engine.

    Attributes:
        max_multiplier: Maximum score multiplier cap.
        multiplier_increment: Amount to increase on failure.
        min_failures_before_update: Minimum failures before guideline update.
        decay_days: Days before applying multiplier decay.
    """

    max_multiplier: float = 3.0
    multiplier_increment: float = 1.2
    min_failures_before_update: int = 3
    decay_days: int = 30


class ACONGuidelineEngine:
    """ACON failure-driven guideline optimization.

    Implements the ACON paper's gradient-free optimization approach
    for improving compression guidelines based on task failures.

    Attributes:
        config: ACON configuration.
        guideline_store: Store for extraction guidelines.
    """

    def __init__(
        self,
        guideline_store: GuidelineStore | None = None,
        config: ACONConfig | None = None,
    ) -> None:
        """Initialize ACON guideline engine.

        Args:
            guideline_store: Optional guideline store.
            config: Optional ACON configuration.
        """
        self.config = config or ACONConfig()
        self.guideline_store = guideline_store or GuidelineStore(
            max_multiplier=self.config.max_multiplier
        )

    def analyze_failure(
        self,
        task_id: str,
        tool_name: str,
        pruned_chunk_ids: list[str],
        pruned_chunk_types: list[str],
    ) -> dict[str, float]:
        """Analyze a compression failure.

        Args:
            task_id: Failed task identifier.
            tool_name: Tool that produced the pruned chunks.
            pruned_chunk_ids: IDs of chunks that were pruned.
            pruned_chunk_types: Types of pruned chunks.

        Returns:
            Dictionary of (tool, chunk_type) -> score_multiplier updates.
        """
        logger.info(f"Analyzing failure for task {task_id}")

        chunk_type_counts: dict[str, int] = {}
        for chunk_type in pruned_chunk_types:
            chunk_type_counts[chunk_type] = chunk_type_counts.get(chunk_type, 0) + 1

        updates: dict[str, float] = {}

        for chunk_type, count in chunk_type_counts.items():
            current = self.guideline_store.get_multiplier(tool_name, chunk_type)

            update_count = self.guideline_store.get_update_count(tool_name, chunk_type)

            if update_count >= self.config.min_failures_before_update:
                new_multiplier = min(
                    current * self.config.multiplier_increment,
                    self.config.max_multiplier,
                )
                self.guideline_store.set_guideline(tool_name, chunk_type, new_multiplier)
                self.guideline_store.increment_update_count(tool_name, chunk_type)
                self.guideline_store.add_evidence(tool_name, chunk_type, task_id)

                updates[f"{tool_name}:{chunk_type}"] = new_multiplier
                logger.info(
                    f"Updated guideline {tool_name}:{chunk_type} "
                    f"from {current:.2f} to {new_multiplier:.2f}"
                )

        return updates

    def apply_decay(self) -> int:
        """Apply decay to all guideline multipliers.

        Returns:
            Number of guidelines decayed.
        """
        before_count = len([k for k, v in self.guideline_store.guidelines.items() if v > 1.0])

        self.guideline_store.decay_multipliers(decay_factor=0.95)

        after_count = len([k for k, v in self.guideline_store.guidelines.items() if v > 1.0])

        return before_count - after_count
