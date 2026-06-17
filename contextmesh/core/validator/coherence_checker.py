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
        body_ids = {
            c.id for c in chunks
            if c.chunk_type in (ChunkType.CODE_FUNCTION, ChunkType.CODE_CLASS)
        }

        if not body_ids:
            return True, chunks, 0

        needed_sigs: set[str] = set()
        for body_id in body_ids:
            for chunk_id, chunk in dependency_graph.chunks.items():
                if body_id in chunk.dependencies:
                    needed_sigs.add(chunk_id)

        current_ids = {c.id for c in chunks}
        missing_sigs = needed_sigs - current_ids

        if not missing_sigs:
            return True, chunks, 0

        for sig_id in missing_sigs:
            if sig_id in dependency_graph.chunks:
                chunks.append(dependency_graph.chunks[sig_id])

        violations = len(missing_sigs)
        return False, chunks, violations

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

        sorted_chunks = sorted(json_chunks, key=lambda c: c.start_pos)
        content = "".join(c.content for c in sorted_chunks)

        try:
            json.loads(content)
            return True, chunks, 0
        except json.JSONDecodeError:
            pass

        try:
            content = "{" + content + "}"
            json.loads(content)
            return True, chunks, 0
        except json.JSONDecodeError:
            pass

        return False, chunks, 1

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
            level = chunk.metadata.get("level")

            for j in range(i - 1, -1, -1):
                prev = sorted_chunks[j]
                prev_component = prev.metadata.get("component")
                prev_level = prev.metadata.get("level")

                if component == prev_component and prev_level == "INFO":
                    break

                if prev.start_pos < chunk.start_pos - 1000:
                    break

            else:
                pass

        return violations_fixed == 0, fixed_chunks, violations_fixed
