"""Recursive JSON chunker.

Segments parsed JSON by key depth. Each chunk is a self-describing,
valid JSON object that maps the dotted path of a subtree to its value:

    {"root.users[3].profile": {"name": "Alice", ...}}

Chunks never overlap: every value in the input appears in exactly one
chunk. Small sibling values are merged into a single chunk (multiple
path keys in one object) to avoid overly granular output.

Architecture:
    JSON string -> json.loads() -> recursive walk over parsed data ->
    subtree serialization (json.dumps) -> non-overlapping chunks

Positions: start_pos is a traversal-order counter, not a byte offset.
Dict traversal follows document order (Python dicts preserve insertion
order), so sorting by start_pos reproduces document order.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkerError,
    ChunkFormat,
    ChunkType,
)
from contextmesh.core.tokenizer import TokenCounter


class JSONChunker(ChunkerBase):
    """Depth-based recursive JSON chunker.

    Containers larger than max_chunk_tokens are split by their children
    (up to max_depth); leaves and small subtrees below min_chunk_tokens
    are merged with their siblings. Every chunk's content is itself
    valid JSON.

    Attributes:
        max_depth: Maximum key depth at which containers are still split.
        min_chunk_tokens: Subtrees at or below this size merge with siblings.
        max_chunk_tokens: Containers at or below this size stay whole.

    Example:
        >>> chunker = JSONChunker(max_depth=4, min_chunk_tokens=30)
        >>> chunks = chunker.chunk('{"users": [{"id": 1, "name": "Alice"}]}')
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.JSON

    def __init__(
        self,
        max_depth: int = 4,
        min_chunk_tokens: int = 30,
        max_chunk_tokens: int = 300,
    ) -> None:
        """Initialize JSON chunker.

        Args:
            max_depth: Maximum depth at which to split containers.
            min_chunk_tokens: Merge subtrees at or below this size.
            max_chunk_tokens: Keep containers at or below this size whole.
        """
        self.max_depth = max_depth
        self.min_chunk_tokens = min_chunk_tokens
        self.max_chunk_tokens = max_chunk_tokens
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment JSON into depth-based, non-overlapping chunks.

        Args:
            content: JSON string to chunk.

        Returns:
            List of Chunks, each containing valid JSON.

        Raises:
            ChunkerError: If content is not valid JSON.
        """
        if not content or not content.strip():
            return []

        try:
            data = json.loads(content)
        except (ValueError, TypeError) as e:
            raise ChunkerError(f"Failed to parse JSON: {e}", format=self.format) from e

        chunks: list[Chunk] = []
        self._position = 0
        self._walk(data, "root", 0, chunks)
        return chunks

    def _walk(
        self,
        value: Any,
        path: str,
        depth: int,
        chunks: list[Chunk],
    ) -> None:
        """Recursively walk a JSON value, emitting chunks.

        Args:
            value: Current JSON value (parsed).
            path: Dotted key path for identification.
            depth: Current nesting depth.
            chunks: Output list to append to.
        """
        rendered = self._render({path: value})
        tokens = self._tokenizer.count(rendered)

        is_splittable = isinstance(value, (dict, list)) and len(value) > 0
        if not is_splittable or depth >= self.max_depth or tokens <= self.max_chunk_tokens:
            self._emit(chunks, rendered, tokens, value, path, depth)
            return

        # Container too large to keep whole: split by children, merging
        # small siblings in document order.
        pending: dict[str, Any] = {}
        pending_tokens = 0

        def flush_pending() -> None:
            nonlocal pending, pending_tokens
            if not pending:
                return
            merged_rendered = self._render(pending)
            self._emit(
                chunks,
                merged_rendered,
                self._tokenizer.count(merged_rendered),
                dict(pending),
                path,
                depth + 1,
                merged_paths=list(pending),
            )
            pending = {}
            pending_tokens = 0

        if isinstance(value, dict):
            items = [(f"{path}.{k}", v) for k, v in value.items()]
        else:
            items = [(f"{path}[{i}]", v) for i, v in enumerate(value)]

        for child_path, child in items:
            child_tokens = self._tokenizer.count(self._render({child_path: child}))
            if child_tokens <= self.min_chunk_tokens or not isinstance(child, (dict, list)):
                if child_tokens > self.max_chunk_tokens:
                    # Oversized primitive (e.g., a huge string): emit alone.
                    flush_pending()
                    self._walk(child, child_path, depth + 1, chunks)
                    continue
                pending[child_path] = child
                pending_tokens += child_tokens
                if pending_tokens >= self.max_chunk_tokens:
                    flush_pending()
            else:
                flush_pending()
                self._walk(child, child_path, depth + 1, chunks)

        flush_pending()

    def _emit(
        self,
        chunks: list[Chunk],
        rendered: str,
        tokens: int,
        value: Any,
        path: str,
        depth: int,
        merged_paths: list[str] | None = None,
    ) -> None:
        """Append a chunk for a rendered subtree.

        Args:
            chunks: Output list.
            rendered: Chunk content (valid JSON).
            tokens: Pre-computed token count of rendered.
            value: The underlying value, for type classification.
            path: Path of the subtree (or parent path for merged chunks).
            depth: Depth of the subtree.
            merged_paths: Paths merged into this chunk, if any.
        """
        if merged_paths is not None:
            chunk_type = ChunkType.JSON_OBJECT
            metadata: dict[str, Any] = {
                "path": path,
                "depth": depth,
                "merged_paths": merged_paths,
            }
        elif isinstance(value, dict):
            chunk_type = ChunkType.JSON_OBJECT
            metadata = {"path": path, "depth": depth, "num_keys": len(value)}
        elif isinstance(value, list):
            chunk_type = ChunkType.JSON_ARRAY
            metadata = {"path": path, "depth": depth, "length": len(value)}
        else:
            chunk_type = ChunkType.JSON_LEAF
            metadata = {"path": path, "depth": depth, "value_type": type(value).__name__}

        chunks.append(
            Chunk(
                id=Chunk.compute_id(rendered),
                content=rendered,
                format=ChunkFormat.JSON,
                chunk_type=chunk_type,
                token_count=tokens,
                start_pos=self._position,
                dependencies=(),
                metadata=metadata,
            )
        )
        self._position += 1

    @staticmethod
    def _render(payload: dict[str, Any]) -> str:
        """Serialize a path->value mapping as compact, readable JSON."""
        return json.dumps(payload, ensure_ascii=False, default=str)
