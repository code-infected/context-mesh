"""Type-aware chunking system for tool output compression.

This module provides the foundational data structures and interfaces for
format-specific chunking of tool outputs. Each chunker implementation
segments tool output into semantically coherent units that can be scored
and selected independently.

Architecture:
    Raw tool output -> Type Detector -> Appropriate Chunker -> List[Chunk]
                                       -> Mixed Chunker (if format mixed)
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator


class ChunkFormat(Enum):
    """Supported tool output formats for chunking.

    Each format requires specialized chunking logic to maintain
    semantic coherence within chunks.
    """

    CODE = "code"
    JSON = "json"
    LOG = "log"
    HTML = "html"
    CSV = "csv"
    SHELL = "shell"
    TEXT = "text"


class ChunkType(Enum):
    """Format-specific chunk type classifications.

    These provide additional semantic information about chunks
    beyond just the format, enabling more intelligent scoring.
    """

    CODE_FUNCTION = "function"
    CODE_CLASS = "class"
    CODE_IMPORT_BLOCK = "import_block"
    CODE_MODULE_DOCSTRING = "module_docstring"
    CODE_BODY = "body"

    JSON_OBJECT = "object"
    JSON_ARRAY = "array"
    JSON_LEAF = "leaf"

    LOG_EVENT = "event"
    LOG_ERROR = "error"
    LOG_TRACE = "traceback"

    HTML_ARTICLE = "article"
    HTML_SECTION = "section"
    HTML_MAIN = "main"
    HTML_NAV = "nav"
    HTML_ASIDE = "aside"
    HTML_DIV = "div"

    CSV_HEADER = "header"
    CSV_ROWS = "rows"

    SHELL_COMMAND = "command"
    SHELL_OUTPUT = "output"

    TEXT_PARAGRAPH = "paragraph"
    TEXT_LINE = "line"


@dataclass(frozen=True)
class Chunk:
    """A semantically coherent unit of tool output.

    Chunks are the atomic unit of compression decisions. Each chunk
    contains enough context to be scored for relevance and validated
    for coherence independently.

    Attributes:
        id: Deterministic hash of content for caching and comparison.
        content: The actual text content of the chunk.
        format: The format category of this chunk.
        chunk_type: Format-specific subtype for scoring hints.
        token_count: Pre-computed token count (cached).
        start_pos: Character position in original output for ordering.
        dependencies: Chunk IDs that this chunk references.
        metadata: Format-specific metadata for coherence validation.

    Example:
        >>> chunk = Chunk(
        ...     id="abc123",
        ...     content="def authenticate_user(token: str) -> bool:",
        ...     format=ChunkFormat.CODE,
        ...     chunk_type=ChunkType.CODE_FUNCTION,
        ...     token_count=12,
        ...     start_pos=150,
        ...     dependencies=[],
        ...     metadata={"function_name": "authenticate_user", "start_line": 5}
        ... )
    """

    id: str
    content: str
    format: ChunkFormat
    chunk_type: ChunkType
    token_count: int
    start_pos: int
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def compute_id(cls, content: str) -> str:
        """Compute a deterministic hash ID for content.

        Args:
            content: The text content to hash.

        Returns:
            A truncated SHA256 hash of the content.
        """
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def with_dependencies(self, deps: list[str]) -> Chunk:
        """Create a new Chunk with additional dependencies.

        Args:
            deps: Additional chunk IDs this chunk depends on.

        Returns:
            A new Chunk instance with updated dependencies.
        """
        return Chunk(
            id=self.id,
            content=self.content,
            format=self.format,
            chunk_type=self.chunk_type,
            token_count=self.token_count,
            start_pos=self.start_pos,
            dependencies=self.dependencies + tuple(deps),
            metadata=self.metadata,
        )


@dataclass
class ScoredChunk:
    """A chunk with its computed relevance score.

    Produced by the scorer after evaluating a chunk against task context.
    Higher scores indicate greater relevance to the current task.

    Attributes:
        chunk: The original chunk data.
        score: Relevance score from embedding similarity (0.0 to 1.0).
        adjusted_score: Score after ACON guideline adjustments.
    """

    chunk: Chunk
    score: float
    adjusted_score: float | None = None

    def __post_init__(self) -> None:
        """Initialize adjusted_score if not provided."""
        if self.adjusted_score is None:
            object.__setattr__(self, "adjusted_score", self.score)


@dataclass
class TaskContext:
    """Context for scoring chunks against a specific task.

    The task context combines the original user task with the current
    agent state to produce a query for relevance scoring.

    Attributes:
        task_description: The original user request.
        tool_name: The tool that produced this output.
        tool_args: The arguments passed to the tool.
        recent_steps: Last N agent reasoning steps for evolving tasks.
    """

    task_description: str
    tool_name: str
    tool_args: dict[str, Any]
    recent_steps: list[str] = field(default_factory=list)

    def to_string(self) -> str:
        """Render task context as a searchable string.

        Combines all context into a single string for embedding.
        Recent steps are weighted more heavily than the original task.

        Returns:
            A space-separated string combining all context fields.
        """
        parts = [self.task_description, f"tool: {self.tool_name}"]

        if self.tool_args:
            args_str = ", ".join(f"{k}={v}" for k, v in self.tool_args.items())
            parts.append(f"args: {args_str}")

        for i, step in enumerate(self.recent_steps[-3:]):
            parts.append(f"step{-(i+1)}: {step}")

        return " | ".join(parts)


@dataclass
class CompressionInput:
    """Input data for a compression operation.

    Encapsulates all information needed to compress a tool output.

    Attributes:
        session_id: Unique session identifier for tracing.
        task_id: Unique task identifier for the agent's task.
        tool_name: Name of the tool that produced the output.
        tool_args: Arguments passed to the tool.
        raw_output: The original tool output text.
        task_description: User's task description.
        recent_steps: Recent agent reasoning steps.
        budget_tokens: Maximum tokens in compressed output.
    """

    session_id: str
    task_id: str
    tool_name: str
    tool_args: dict[str, Any]
    raw_output: str
    task_description: str
    recent_steps: list[str] = field(default_factory=list)
    budget_tokens: int = 8000


@dataclass
class CompressionOutput:
    """Result of a compression operation.

    Contains the compressed output and metadata about the compression.

    Attributes:
        compressed_output: The selected chunks concatenated.
        original_tokens: Token count before compression.
        compressed_tokens: Token count after compression.
        compression_ratio: compressed_tokens / original_tokens.
        chunks_selected: Number of chunks in output.
        chunks_total: Total chunks available.
        trace_id: ID for correlating with trace store.
        chunk_types_selected: Types of chunks selected (for observability).
    """

    compressed_output: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    chunks_selected: int
    chunks_total: int
    trace_id: str | None = None
    chunk_types_selected: list[str] = field(default_factory=list)


class ChunkerBase(ABC):
    """Abstract base class for format-specific chunkers.

    All chunkers must implement the chunking logic for their specific
    format. The base class enforces the interface contract.

    Attributes:
        format: The ChunkFormat this chunker handles.
        min_chunk_tokens: Minimum tokens for a valid chunk.
        max_chunk_tokens: Maximum tokens before splitting.

    Example:
        >>> class MyChunker(ClickuperBase):
        ...     format = ChunkFormat.CODE
        ...
        ...     def chunk(self, content: str) -> list[Chunk]:
        ...         # Implementation
        ...         return chunks
    """

    format: ClassVar[ChunkFormat]
    min_chunk_tokens: ClassVar[int] = 20
    max_chunk_tokens: ClassVar[int] = 300

    @abstractmethod
    def chunk(self, content: str) -> list[Chunk]:
        """Segment content into semantically coherent chunks.

        Args:
            content: The raw tool output to chunk.

        Returns:
            A list of Chunks in their original order.

        Raises:
            ChunkerError: If chunking fails.
        """

    def chunk_iter(self, content: str) -> Iterator[Chunk]:
        """Iterate over chunks without materializing the full list.

        Default implementation calls chunk() and yields.
        Subclasses can override for memory efficiency.

        Args:
            content: The raw tool output to chunk.

        Yields:
            Chunks in original order.
        """
        yield from self.chunk(content)


class ChunkerError(Exception):
    """Raised when chunking fails.

    This exception wraps format-specific errors with context
    about the chunking operation that failed.
    """

    def __init__(self, message: str, format: ChunkFormat | None = None) -> None:
        super().__init__(message)
        self.format = format
