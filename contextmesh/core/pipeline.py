"""Compression pipeline orchestrator.

Orchestrates the full compression flow:
    Type detection -> Chunker -> Dependency graph ->
    Scorer -> Extractor -> Validator -> Output

Architecture:
    Input -> chunker -> scorer -> extractor -> validator -> Output
              |          |          |           |
              v          v          v           v
           chunks    scored    selected      validated
                      chunks    chunks         chunks
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkFormat,
    CompressionInput,
    CompressionOutput,
    TaskContext,
)
from contextmesh.core.chunker.code_chunker import CodeChunker
from contextmesh.core.chunker.dependency_graph import DependencyGraph
from contextmesh.core.chunker.html_chunker import HTMLChunker
from contextmesh.core.chunker.json_chunker import JSONChunker
from contextmesh.core.chunker.log_chunker import LogChunker
from contextmesh.core.chunker.mixed_chunker import MixedChunker
from contextmesh.core.chunker.shell_chunker import ShellChunker
from contextmesh.core.extractor.budget_extractor import BudgetExtractor, ExtractorConfig
from contextmesh.core.scorer.cache import EmbeddingCache
from contextmesh.core.scorer.embed_scorer import EmbedScorer
from contextmesh.core.scorer.guideline_adjuster import GuidelineAdjuster, GuidelineStore
from contextmesh.core.tokenizer import TokenCounter
from contextmesh.core.validator.coherence_checker import CoherenceChecker

if TYPE_CHECKING:
    from contextmesh.core.chunker.base import ChunkerBase

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the compression pipeline.

    Attributes:
        default_budget_tokens: Default token budget per call.
        max_overhead_ms: Abort if compression exceeds this.
        min_compression_ratio: Skip compression if ratio above this.
        enable_coherence_validation: Whether to run validator.
    """

    default_budget_tokens: int = 8000
    max_overhead_ms: int = 80
    min_compression_ratio: float = 0.3
    enable_coherence_validation: bool = True


class CompressionPipeline:
    """Main compression pipeline orchestrator.

    Coordinates all compression components into a single
    compress() call that returns a CompressionOutput.

    Attributes:
        config: Pipeline configuration.
        chunker: Format-specific or mixed chunker.
        scorer: Embedding-based relevance scorer.
        guideline_adjuster: ACON guideline adjuster.
        extractor: Budget-constrained extractor.
        validator: Coherence validator.
        tokenizer: Token counter.

    Example:
        >>> pipeline = CompressionPipeline()
        >>> inp = CompressionInput(
        ...     session_id="s1",
        ...     task_id="t1",
        ...     tool_name="read_file",
        ...     tool_args={"path": "/src/main.py"},
        ...     raw_output="def foo(): pass",
        ...     task_description="find all functions",
        ...     budget_tokens=6000,
        ... )
        >>> out = pipeline.compress(inp)
    """

    def __init__(
        self,
        config: PipelineConfig | None = None,
        chunker: ChunkerBase | None = None,
        scorer: EmbedScorer | None = None,
        guideline_adjuster: GuidelineAdjuster | None = None,
        extractor: BudgetExtractor | None = None,
        validator: CoherenceChecker | None = None,
    ) -> None:
        """Initialize compression pipeline.

        Args:
            config: Optional pipeline configuration.
            chunker: Optional chunker override.
            scorer: Optional scorer override.
            guideline_adjuster: Optional guideline adjuster override.
            extractor: Optional extractor override.
            validator: Optional validator override.
        """
        self.config = config or PipelineConfig()
        self.chunker = chunker or MixedChunker()
        self.scorer = scorer or EmbedScorer()
        self.guideline_adjuster = guideline_adjuster or GuidelineAdjuster()
        self.extractor = extractor or BudgetExtractor(ExtractorConfig())
        self.validator = validator or CoherenceChecker()
        self.tokenizer = TokenCounter.get_default()

    def compress(self, inp: CompressionInput) -> CompressionOutput:
        """Compress tool output under token budget.

        Args:
            inp: Compression input with tool output and context.

        Returns:
            Compression output with compressed content and metadata.
        """
        import time

        start_time = time.monotonic()

        original_tokens = self.tokenizer.count(inp.raw_output)

        if original_tokens < 1000:
            return CompressionOutput(
                compressed_output=inp.raw_output,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                chunks_selected=1,
                chunks_total=1,
            )

        budget = inp.budget_tokens or self.config.default_budget_tokens

        chunks = self._chunk(inp.raw_output)

        if not chunks:
            return CompressionOutput(
                compressed_output=inp.raw_output,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                chunks_selected=0,
                chunks_total=0,
            )

        dep_graph = self._build_dependency_graph(chunks)

        task_context = TaskContext(
            task_description=inp.task_description,
            tool_name=inp.tool_name,
            tool_args=inp.tool_args,
            recent_steps=inp.recent_steps,
        )

        scored_chunks = self.scorer.score_chunks(chunks, task_context)

        scored_chunks = self.guideline_adjuster.apply_guidelines(
            scored_chunks, inp.tool_name
        )

        selected = self.extractor.extract(
            scored_chunks, budget, dep_graph, self._get_token_count
        )

        if self.config.enable_coherence_validation:
            validated = self.validator.validate(selected, budget, dep_graph)
            selected = validated.chunks
        else:
            validated = None

        compressed_output = self._reconstruct_output(selected)

        compressed_tokens = self.tokenizer.count(compressed_output)
        ratio = compressed_tokens / original_tokens if original_tokens > 0 else 1.0

        if ratio > self.config.min_compression_ratio:
            return CompressionOutput(
                compressed_output=inp.raw_output,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                chunks_selected=1,
                chunks_total=len(chunks),
            )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        if elapsed_ms > self.config.max_overhead_ms:
            logger.warning(
                f"Compression exceeded time budget: {elapsed_ms:.1f}ms > "
                f"{self.config.max_overhead_ms}ms"
            )

        return CompressionOutput(
            compressed_output=compressed_output,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=ratio,
            chunks_selected=len(selected),
            chunks_total=len(chunks),
            chunk_types_selected=[c.chunk_type.value for c in selected],
        )

    def _chunk(self, content: str) -> list[Chunk]:
        """Chunk content using appropriate chunker.

        Args:
            content: Raw tool output.

        Returns:
            List of chunks.
        """
        detected_format = self._detect_format(content)

        if detected_format == ChunkFormat.CODE:
            chunker = CodeChunker("python")
            return chunker.chunk(content)
        elif detected_format == ChunkFormat.JSON:
            return JSONChunker().chunk(content)
        elif detected_format == ChunkFormat.HTML:
            return HTMLChunker().chunk(content)
        elif detected_format == ChunkFormat.LOG:
            return LogChunker().chunk(content)
        elif detected_format == ChunkFormat.SHELL:
            return ShellChunker().chunk(content)
        else:
            return self.chunker.chunk(content)

    def _detect_format(self, content: str) -> ChunkFormat:
        """Detect output format from content.

        Args:
            content: Raw tool output.

        Returns:
            Detected ChunkFormat.
        """
        stripped = content.strip()

        if stripped.startswith("{") or stripped.startswith("["):
            return ChunkFormat.JSON

        if stripped.startswith("<"):
            if any(
                stripped.lower().startswith(tag)
                for tag in ["<html", "<div", "<article", "<section", "<main"]
            ):
                return ChunkFormat.HTML

        code_indicators = [
            "def ", "class ", "function ", "import ", "const ", "let ", "var ",
            "public ", "private ", "async ", "await "
        ]
        if any(stripped.startswith(ind) for ind in code_indicators):
            return ChunkFormat.CODE

        import re

        if re.search(r"^\$ ", content, re.MULTILINE):
            return ChunkFormat.SHELL

        log_patterns = [
            r"^\d{4}-\d{2}-\d{2}",
            r"^\d{2}/\d{2}/\d{4}",
            r"\bINFO\b",
            r"\bERROR\b",
            r"\bWARN\b",
        ]
        if any(re.search(p, content, re.MULTILINE) for p in log_patterns):
            return ChunkFormat.LOG

        return ChunkFormat.TEXT

    def _build_dependency_graph(self, chunks: list[Chunk]) -> DependencyGraph:
        """Build dependency graph from chunks.

        Args:
            chunks: Chunk list.

        Returns:
            DependencyGraph with all chunks added.
        """
        graph = DependencyGraph()
        for chunk in chunks:
            graph.add_chunk(chunk)
        return graph

    def _get_token_count(self, chunk_id: str) -> int:
        """Get token count for a chunk ID.

        This is a simple lookup. In production, this would
        use the chunk store to look up actual token counts.

        Args:
            chunk_id: Chunk ID.

        Returns:
            Token count (0 as fallback).
        """
        return 0

    def _reconstruct_output(self, chunks: list[Chunk]) -> str:
        """Reconstruct compressed output from chunks.

        Args:
            chunks: Selected chunks.

        Returns:
            Reconstructed text content.
        """
        if not chunks:
            return ""

        sorted_chunks = sorted(chunks, key=lambda c: c.start_pos)
        return "\n".join(c.content for c in sorted_chunks)
