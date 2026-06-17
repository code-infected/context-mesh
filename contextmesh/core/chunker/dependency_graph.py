"""Dependency graph builder for chunks.

Builds a directed graph representing dependencies between chunks.
This enables the extractor to ensure selected chunks don't reference
pruned chunks, which would cause runtime errors for the agent.

The dependency graph is used by the BudgetExtractor to:
1. Transitively include dependencies when selecting a chunk
2. Avoid selecting chunks whose unselected dependencies would cause issues
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextmesh.core.chunker.base import Chunk


@dataclass
class DependencyGraph:
    """Directed graph of chunk dependencies.

    Each chunk may depend on zero or more other chunks. The graph
    supports transitive dependency resolution for selecting entire
    dependency chains.

    Attributes:
        chunks: Map from chunk ID to Chunk object.
        outgoing: Map from chunk ID to set of chunk IDs it depends on.
        incoming: Map from chunk ID to set of chunk IDs that depend on it.
    """

    chunks: dict[str, Chunk] = field(default_factory=dict)
    outgoing: dict[str, set[str]] = field(default_factory=lambda: {})
    incoming: dict[str, set[str]] = field(default_factory=lambda: {})

    def add_chunk(self, chunk: Chunk) -> None:
        """Add a chunk and its direct dependencies to the graph.

        Args:
            chunk: The chunk to add.
        """
        self.chunks[chunk.id] = chunk
        if chunk.id not in self.outgoing:
            self.outgoing[chunk.id] = set()
        if chunk.id not in self.incoming:
            self.incoming[chunk.id] = set()

        for dep_id in chunk.dependencies:
            self.outgoing[chunk.id].add(dep_id)
            if dep_id not in self.incoming:
                self.incoming[dep_id] = set()
            self.incoming[dep_id].add(chunk.id)
            if dep_id not in self.chunks:
                self.chunks[dep_id] = self._placeholder_chunk(dep_id)

    def _placeholder_chunk(self, chunk_id: str) -> Chunk:
        """Create a placeholder chunk for external references.

        When a chunk references another chunk not in our set,
        we create a placeholder to track the dependency.

        Args:
            chunk_id: ID of the referenced chunk.

        Returns:
            A minimal placeholder Chunk.
        """
        from contextmesh.core.chunker.base import Chunk, ChunkFormat, ChunkType

        return Chunk(
            id=chunk_id,
            content="",
            format=ChunkFormat.TEXT,
            chunk_type=ChunkType.TEXT_PARAGRAPH,
            token_count=0,
            start_pos=-1,
        )

    def get_dependencies(self, chunk_id: str, transitive: bool = True) -> set[str]:
        """Get all chunks that a chunk depends on.

        Args:
            chunk_id: The chunk to get dependencies for.
            transitive: If True, include transitive dependencies.

        Returns:
            Set of chunk IDs that chunk_id depends on.
        """
        if chunk_id not in self.outgoing:
            return set()

        if not transitive:
            return self.outgoing[chunk_id].copy()

        visited: set[str] = set()
        queue: deque[str] = deque([chunk_id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue

            visited.add(current)
            for dep in self.outgoing.get(current, []):
                if dep not in visited:
                    queue.append(dep)

        visited.discard(chunk_id)
        return visited

    def get_dependents(self, chunk_id: str, transitive: bool = True) -> set[str]:
        """Get all chunks that depend on a chunk.

        Args:
            chunk_id: The chunk to get dependents for.
            transitive: If True, include transitive dependents.

        Returns:
            Set of chunk IDs that depend on chunk_id.
        """
        if chunk_id not in self.incoming:
            return set()

        if not transitive:
            return self.incoming[chunk_id].copy()

        visited: set[str] = set()
        queue: deque[str] = deque([chunk_id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue

            visited.add(current)
            for dep in self.incoming.get(current, []):
                if dep not in visited:
                    queue.append(dep)

        visited.discard(chunk_id)
        return visited

    def can_select(
        self, chunk_ids: set[str], budget: int, get_token_count: callable
    ) -> tuple[bool, int]:
        """Check if a set of chunks can be selected within budget.

        Computes the total token cost including all transitive dependencies
        that would need to be included.

        Args:
            chunk_ids: Chunks being considered for selection.
            budget: Available token budget.
            get_token_count: Function to get token count for a chunk ID.

        Returns:
            Tuple of (can_select, total_tokens_needed).
        """
        all_needed: set[str] = set()

        for chunk_id in chunk_ids:
            deps = self.get_dependencies(chunk_id, transitive=True)
            all_needed.update(deps)
            all_needed.add(chunk_id)

        total_tokens = sum(
            get_token_count(chunk_id) for chunk_id in all_needed if chunk_id in self.chunks
        )

        return total_tokens <= budget, total_tokens

    def topological_order(self) -> list[str]:
        """Return chunks in topological order (dependencies first).

        This order preserves the invariant that all dependencies
        of a chunk appear before the chunk itself.

        Returns:
            List of chunk IDs in topological order.
        """
        in_degree: dict[str, int] = {cid: 0 for cid in self.chunks}
        for deps in self.outgoing.values():
            for dep in deps:
                if dep in in_degree:
                    in_degree[dep] += 1

        queue: deque[str] = deque([cid for cid, degree in in_degree.items() if degree == 0])
        result: list[str] = []

        while queue:
            current = queue.popleft()
            result.append(current)

            for dependent in self.incoming.get(current, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return result

    def original_order(self, chunk_ids: list[str]) -> list[str]:
        """Sort chunk IDs by their original position in the output.

        Preserves the narrative/logical flow of the original output
        after extraction selection.

        Args:
            chunk_ids: Chunk IDs to sort.

        Returns:
            Chunk IDs sorted by start_pos ascending.
        """
        return sorted(
            chunk_ids,
            key=lambda cid: self.chunks[cid].start_pos if cid in self.chunks else float("inf"),
        )
