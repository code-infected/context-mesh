"""ContextMesh Python SDK.

Provides a simple interface for integrating ContextMesh
compression into custom agent loops.

Usage:
    from contextmesh.proxy.sdk.python.contextmesh import ContextMesh

    cm = ContextMesh(task_description="fix auth bug", budget_tokens=8000)
    raw_result = file_tool.read("/src/auth.py")
    compressed = cm.compress(output=raw_result, tool_name="read_file")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompressionResult:
    """Result from a compression operation.

    Attributes:
        content: The compressed output text.
        metadata: Compression statistics and metadata.
    """

    content: str
    metadata: CompressionMetadata


@dataclass
class CompressionMetadata:
    """Metadata about a compression operation.

    Attributes:
        original_tokens: Token count before compression.
        compressed_tokens: Token count after compression.
        compression_ratio: Ratio of compressed to original tokens.
        chunks_selected: Number of chunks in output.
        chunks_total: Total chunks available.
    """

    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    chunks_selected: int
    chunks_total: int


class ContextMesh:
    """Python SDK for ContextMesh compression.

    Provides a simple compress() method that wraps the core
    compression pipeline. Designed for custom agent loops
    that don't use MCP.

    Attributes:
        task_description: The current task description for scoring.
        budget_tokens: Default token budget per compression.
        config_path: Optional path to config.yaml.

    Example:
        >>> cm = ContextMesh(task_description="refactor auth", budget_tokens=8000)
        >>> result = cm.compress(output=file_content, tool_name="read_file")
        >>> print(f"Reduced from {result.metadata.original_tokens} to {result.metadata.compressed_tokens}")
    """

    def __init__(
        self,
        task_description: str,
        budget_tokens: int = 8000,
        config_path: str | None = None,
    ) -> None:
        """Initialize ContextMesh SDK.

        Args:
            task_description: Current task description.
            budget_tokens: Token budget per call.
            config_path: Optional config file path.
        """
        self.task_description = task_description
        self.budget_tokens = budget_tokens
        self.config_path = config_path
        self._pipeline = self._init_pipeline()

    def _init_pipeline(self) -> Any:
        """Initialize the compression pipeline.

        Returns:
            CompressionPipeline instance.
        """
        from contextmesh.core.pipeline import CompressionPipeline

        return CompressionPipeline()

    def compress(
        self,
        output: str,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
        budget_tokens: int | None = None,
    ) -> CompressionResult:
        """Compress tool output.

        Args:
            output: Raw tool output text.
            tool_name: Name of the tool that produced the output.
            tool_args: Arguments passed to the tool.
            budget_tokens: Override default budget for this call.

        Returns:
            CompressionResult with compressed content and metadata.
        """
        from contextmesh.core.chunker.base import CompressionInput

        inp = CompressionInput(
            session_id="sdk-session",
            task_id="sdk-task",
            tool_name=tool_name,
            tool_args=tool_args or {},
            raw_output=output,
            task_description=self.task_description,
            budget_tokens=budget_tokens or self.budget_tokens,
        )

        result = self._pipeline.compress(inp)

        return CompressionResult(
            content=result.compressed_output,
            metadata=CompressionMetadata(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                compression_ratio=result.compression_ratio,
                chunks_selected=result.chunks_selected,
                chunks_total=result.chunks_total,
            ),
        )

    def report_outcome(
        self,
        task_id: str,
        outcome: str,
        failure_reason: str | None = None,
    ) -> None:
        """Report task outcome for ACON feedback loop.

        Args:
            task_id: Task identifier.
            outcome: "success" or "failed".
            failure_reason: Error message if failed.
        """
        logger.info(f"Task outcome reported: {task_id} -> {outcome}")
