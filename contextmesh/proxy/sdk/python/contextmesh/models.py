"""ContextMesh Python SDK models."""

from dataclasses import dataclass


@dataclass
class CompressionMetadata:
    """Metadata about a compression operation."""

    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    chunks_selected: int
    chunks_total: int


@dataclass
class CompressionResult:
    """Result from a compression operation."""

    content: str
    metadata: CompressionMetadata
