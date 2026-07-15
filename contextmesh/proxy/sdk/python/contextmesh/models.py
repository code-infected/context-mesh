"""ContextMesh Python SDK models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompressionMetadata:
    """Metadata about a compression operation.

    Attributes:
        original_tokens: Token count before compression.
        compressed_tokens: Token count after compression.
        compression_ratio: Ratio of compressed to original tokens.
        chunks_selected: Number of chunks in output.
        chunks_total: Total chunks available.
        trace_id: Trace identifier for the ACON feedback loop.
        compression_failed: True when the raw output was returned
            because compression failed or was skipped.
    """

    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    chunks_selected: int
    chunks_total: int
    trace_id: str | None = None
    compression_failed: bool = False


@dataclass
class CompressionResult:
    """Result from a compression operation.

    Attributes:
        content: The compressed output text.
        metadata: Compression statistics and metadata.
        task_id: Task identifier assigned to this call; pass it to
            report_outcome() when the task finishes.
    """

    content: str
    metadata: CompressionMetadata
    task_id: str = field(default="")
