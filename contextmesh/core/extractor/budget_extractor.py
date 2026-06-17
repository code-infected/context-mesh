"""Budget-constrained chunk extractor.

Selects chunks under a token budget while respecting dependency
constraints. Uses a greedy algorithm with dependency resolution.

Architecture:
    Scored chunks -> sort by score -> greedy selection with deps ->
    selected chunks under budget
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from contextmesh.core.chunker.base import Chunk, ScoredChunk

if TYPE_CHECKING:
    from contextmesh.core.chunker.dependency_graph import DependencyGraph


@dataclass
class ExtractorConfig:
    """Configuration for budget extractor.

    Attributes:
        dependency_budget_slack: Allow budget to exceed by this fraction for deps.
        max_coherence_iterations: Max validator retry attempts.
    """

    dependency_budget_slack: float = 0.15
    max_coherence_iterations: int = 3


class BudgetExtractor:
    """Budget-constrained extractor with dependency awareness.

    Selects chunks that maximize relevance under a token budget,
    while ensuring all dependencies of selected chunks are also selected.

    The algorithm:
        1. Sort chunks by (adjusted) score descending
        2. Greedily select chunks if:
           a. Chunk fits in remaining budget
           b. All transitive dependencies fit in budget + slack
        3. Order selected chunks by original position

    Attributes:
        config: Extractor configuration.

    Example:
        >>> extractor = BudgetExtractor()
        >>> selected = extractor.extract(scored_chunks, budget=8000, deps=graph)
    """

    def __init__(self, config: ExtractorConfig | None = None) -> None:
        """Initialize budget extractor.

        Args:
            config: Optional configuration.
        """
        self.config = config or ExtractorConfig()

    def extract(
        self,
        scored_chunks: list[ScoredChunk],
        budget: int,
        dependency_graph: DependencyGraph,
        get_token_count: Callable[[str], int] | None = None,
    ) -> list[Chunk]:
        """Extract chunks under budget with dependency constraints.

        Args:
            scored_chunks: Chunks sorted by adjusted score descending.
            budget: Maximum tokens allowed.
            dependency_graph: Chunk dependency graph.
            get_token_count: Function to get token count for chunk ID.

        Returns:
            Selected chunks in original order.
        """
        if get_token_count is None:
            get_token_count = self._default_token_count

        if not scored_chunks:
            return []

        selected: set[str] = set()
        remaining_budget = budget
        slack = int(budget * self.config.dependency_budget_slack)

        for sc in scored_chunks:
            chunk_id = sc.chunk.id

            if chunk_id in selected:
                continue

            deps = dependency_graph.get_dependencies(chunk_id, transitive=True)
            all_needed = deps | {chunk_id}

            total_tokens = sum(
                get_token_count(dep_id) for dep_id in all_needed
                if dep_id in dependency_graph.chunks
            )

            if total_tokens <= remaining_budget + slack:
                selected.update(all_needed)
                remaining_budget -= total_tokens

        selected_list = dependency_graph.original_order(list(selected))
        return [dependency_graph.chunks[cid] for cid in selected_list if cid in dependency_graph.chunks]

    def _default_token_count(self, chunk_id: str) -> int:
        """Default token count getter (raises error).

        Args:
            chunk_id: Chunk ID.

        Returns:
            Token count (always 0, use proper getter).

        Raises:
            ValueError: Always raised when called.
        """
        raise ValueError("Must provide get_token_count function")


def create_dependency_aware_extractor() -> BudgetExtractor:
    """Create extractor with default configuration.

    Returns:
        Configured BudgetExtractor instance.
    """
    return BudgetExtractor(ExtractorConfig())
