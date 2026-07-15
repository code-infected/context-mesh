"""Coherence validation for extracted chunks.

Ensures selected chunks form a readable, coherent output.
Format-specific rules prevent pathological cases like
function bodies without signatures or JSON without closing braces.

Architecture:
    Selected chunks -> format-specific validation ->
    fix violations -> retry up to N times -> validated chunks
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from contextmesh.core.chunker.base import Chunk, ChunkFormat, ChunkType

if TYPE_CHECKING:
    from contextmesh.core.chunker.dependency_graph import DependencyGraph


@dataclass
class CoherenceResult:
    """Result of coherence validation.

    Attributes:
        valid: Whether coherence was achieved.
        chunks: Chunks after validation.
        violations_fixed: Number of violations corrected.
        iterations: Number of validation iterations run.
    """

    valid: bool
    chunks: list[Chunk]
    violations_fixed: int
    iterations: int


class CoherenceChecker:
    """Format-specific coherence validator.

    Checks and fixes coherence violations based on format rules.
    For code: ensures function signatures accompany bodies.
    For JSON: ensures valid JSON after reconstruction.
    For logs: ensures context precedes error lines.

    Attributes:
        max_iterations: Max validation retry attempts.
        budget_slack: Allowed budget exceed for coherence fixes.
    """

    def __init__(
        self,
        max_iterations: int = 3,
        budget_slack: float = 0.15,
    ) -> None:
        """Initialize coherence checker.

        Args:
            max_iterations: Maximum validation retries.
            budget_slack: Allowed budget exceed fraction.
        """
        self.max_iterations = max_iterations
        self.budget_slack = budget_slack

    def validate(
        self,
        chunks: list[Chunk],
        budget: int,
        dependency_graph: DependencyGraph,
    ) -> CoherenceResult:
        """Validate and fix coherence violations.

        Args:
            chunks: Selected chunks to validate.
            budget: Original token budget.
            dependency_graph: Dependency graph for the chunks.

        Returns:
            Validation result with fixed chunks.
        """
        if not chunks:
            return CoherenceResult(valid=True, chunks=[], violations_fixed=0, iterations=0)

        violations_fixed = 0
        current_chunks = list(chunks)
        iterations = 0

        while iterations < self.max_iterations:
            valid, fixed, num_fixed = self._validate_once(
                current_chunks, dependency_graph
            )

            violations_fixed += num_fixed

            if valid:
                return CoherenceResult(
                    valid=True,
                    chunks=current_chunks,
                    violations_fixed=violations_fixed,
                    iterations=iterations + 1,
                )

            current_chunks = fixed
            iterations += 1

        return CoherenceResult(
            valid=False,
            chunks=current_chunks,
            violations_fixed=violations_fixed,
            iterations=iterations,
        )

    def _validate_once(
        self,
        chunks: list[Chunk],
        dependency_graph: DependencyGraph,
    ) -> tuple[bool, list[Chunk], int]:
        """Run one validation pass.

        Args:
            chunks: Chunks to validate.
            dependency_graph: Dependency graph.

        Returns:
            Tuple of (is_valid, fixed_chunks, num_violations_fixed).
        """
        formats = {c.format for c in chunks}
        violations_fixed = 0
        fixed_chunks = list(chunks)

        if ChunkFormat.CODE in formats:
            valid, fixed, num = self._validate_code(fixed_chunks, dependency_graph)
            if not valid:
                violations_fixed += num
                fixed_chunks = fixed

        if ChunkFormat.JSON in formats:
            valid, fixed, num = self._validate_json(fixed_chunks)
            if not valid:
                violations_fixed += num
                fixed_chunks = fixed

        if ChunkFormat.LOG in formats:
            valid, fixed, num = self._validate_logs(fixed_chunks)
            if not valid:
                violations_fixed += num
                fixed_chunks = fixed

        is_valid = violations_fixed == 0
        return is_valid, fixed_chunks, violations_fixed

    def _validate_code(
        self,
        chunks: list[Chunk],
        dependency_graph: DependencyGraph,
    ) -> tuple[bool, list[Chunk], int]:
        """Validate code chunk coherence.

        Ensures function signatures are present when bodies are selected.

        Args:
            chunks: Code chunks to validate.
            dependency_graph: Dependency graph.

        Returns:
            Tuple of (is_valid, fixed_chunks, violations_fixed).
        """
        code_ids = {c.id for c in chunks if c.format == ChunkFormat.CODE}
        if not code_ids:
            return True, chunks, 0

        # A selected chunk must not reference a pruned chunk it depends
        # on (a split-function body without its signature head, a caller
        # without its helper). Force-include the missing *dependencies*
        # — never the dependents: pulling in callers of a selected chunk
        # cascades until the whole file is re-included.
        current_ids = {c.id for c in chunks}
        missing: set[str] = set()
        for chunk in chunks:
            if chunk.format != ChunkFormat.CODE:
                continue
            for dep_id in chunk.dependencies:
                if dep_id not in current_ids and dep_id in dependency_graph.chunks:
                    missing.add(dep_id)

        if not missing:
            return True, chunks, 0

        for dep_id in missing:
            chunks.append(dependency_graph.chunks[dep_id])

        return False, chunks, len(missing)

    def _validate_json(
        self,
        chunks: list[Chunk],
    ) -> tuple[bool, list[Chunk], int]:
        """Validate JSON chunk coherence.

        Ensures reconstructed content is valid JSON.

        Args:
            chunks: JSON chunks to validate.

        Returns:
            Tuple of (is_valid, fixed_chunks, violations_fixed).
        """
        json_chunks = [c for c in chunks if c.format == ChunkFormat.JSON]
        if not json_chunks:
            return True, chunks, 0

        # The JSON chunker guarantees each chunk is a self-contained valid
        # JSON object ({"<path>": <value>}), so the selection is coherent
        # iff every chunk parses on its own. Chunks that fail to parse
        # (e.g., produced by the mixed chunker's boundary detection) are
        # dropped rather than emitting broken JSON to the agent.
        broken = []
        for chunk in json_chunks:
            try:
                json.loads(chunk.content)
            except json.JSONDecodeError:
                broken.append(chunk.id)

        if not broken:
            return True, chunks, 0

        fixed = [c for c in chunks if c.id not in set(broken)]
        return False, fixed, len(broken)

    def _validate_logs(
        self,
        chunks: list[Chunk],
    ) -> tuple[bool, list[Chunk], int]:
        """Validate log chunk coherence.

        Ensures error logs have preceding context.

        Args:
            chunks: Log chunks to validate.

        Returns:
            Tuple of (is_valid, fixed_chunks, violations_fixed).
        """
        error_chunks = [
            c for c in chunks
            if c.chunk_type == ChunkType.LOG_ERROR
        ]

        if not error_chunks:
            return True, chunks, 0

        violations_fixed = 0
        fixed_chunks = list(chunks)
        sorted_chunks = sorted(fixed_chunks, key=lambda c: c.start_pos)

        for i, chunk in enumerate(sorted_chunks):
            if chunk.chunk_type != ChunkType.LOG_ERROR:
                continue

            component = chunk.metadata.get("component")
            has_context = False

            for j in range(i - 1, -1, -1):
                prev = sorted_chunks[j]
                prev_component = prev.metadata.get("component")
                prev_level = prev.metadata.get("level")

                if component == prev_component and prev_level == "INFO":
                    has_context = True
                    break

                if prev.start_pos < chunk.start_pos - 1000:
                    break

            if not has_context:
                violations_fixed += 1

        return violations_fixed == 0, fixed_chunks, violations_fixed
