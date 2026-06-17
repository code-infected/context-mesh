"""Recursive JSON chunker.

Segments JSON by key depth, creating parent-child relationships
that preserve structure. Small leaf values are merged with parents
to avoid overly granular chunks.

Architecture:
    JSON string -> json.loads() -> recursive segmentation ->
    depth-based chunk creation -> structure-preserving chunks
"""

from __future__ import annotations

from typing import Any, ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkFormat,
    ChunkType,
    ChunkerBase,
    ChunkerError,
)
from contextmesh.core.tokenizer import TokenCounter


class JSONChunker(ChunkerBase):
    """Depth-based recursive JSON chunker.

    Creates chunks at configurable key depth. Leaf values below
    a token threshold are merged with parent chunks. Always
    preserves valid JSON structure.

    Attributes:
        max_depth: Key depth at which to create chunks.
        min_chunk_tokens: Merge leaves below this size.

    Example:
        >>> chunker = JSONChunker(max_depth=4, min_chunk_tokens=30)
        >>> data = {"users": [{"id": 1, "name": "Alice"}]}
        >>> chunks = chunker.chunk('{"users": [{"id": 1, "name": "Alice"}]}')
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.JSON

    def __init__(
        self,
        max_depth: int = 4,
        min_chunk_tokens: int = 30,
    ) -> None:
        """Initialize JSON chunker.

        Args:
            max_depth: Depth at which to create chunks (1 = top-level keys).
            min_chunk_tokens: Minimum tokens for a valid leaf chunk.
        """
        self.max_depth = max_depth
        self.min_chunk_tokens = min_chunk_tokens
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment JSON into depth-based chunks.

        Args:
            content: JSON string to chunk.

        Returns:
            List of Chunks preserving JSON structure.

        Raises:
            ChunkerError: If content is not valid JSON.
        """
        try:
            data = self._parse_json(content)
        except Exception as e:
            raise ChunkerError(f"Failed to parse JSON: {e}", format=self.format) from e

        if data is None:
            return []

        chunks: list[Chunk] = []
        self._chunk_value(
            data,
            content,
            path="root",
            depth=0,
            start_pos=0,
            chunks=chunks,
        )

        return self._sort_chunks(chunks)

    def _parse_json(self, content: str) -> Any:
        """Parse JSON content.

        Args:
            content: JSON string.

        Returns:
            Parsed JSON object.
        """
        import json

        return json.loads(content)

    def _chunk_value(
        self,
        value: Any,
        full_content: str,
        path: str,
        depth: int,
        start_pos: int,
        chunks: list[Chunk],
    ) -> None:
        """Recursively chunk a JSON value.

        Args:
            value: Current JSON value.
            full_content: Original JSON string for position tracking.
            path: Current key path for identification.
            depth: Current nesting depth.
            start_pos: Character position in original string.
            chunks: Output list to append to.
        """
        if isinstance(value, dict):
            self._chunk_object(value, full_content, path, depth, start_pos, chunks)
        elif isinstance(value, list):
            self._chunk_array(value, full_content, path, depth, start_pos, chunks)
        else:
            self._chunk_leaf(value, full_content, path, depth, start_pos, chunks)

    def _chunk_object(
        self,
        obj: dict[str, Any],
        full_content: str,
        path: str,
        depth: int,
        start_pos: int,
        chunks: list[Chunk],
    ) -> None:
        """Chunk a JSON object.

        Args:
            obj: JSON object.
            full_content: Original JSON string.
            path: Current path.
            depth: Current depth.
            start_pos: Character position.
            chunks: Output list.
        """
        if depth >= self.max_depth:
            chunk_content = self._extract_json_segment(full_content, start_pos)
            token_count = self._tokenizer.count(chunk_content)

            chunks.append(
                Chunk(
                    id=Chunk.compute_id(chunk_content),
                    content=chunk_content,
                    format=ChunkFormat.JSON,
                    chunk_type=ChunkType.JSON_OBJECT,
                    token_count=token_count,
                    start_pos=start_pos,
                    dependencies=self._get_parent_dependencies(path),
                    metadata={"path": path, "depth": depth, "num_keys": len(obj)},
                )
            )
            return

        for key, val in obj.items():
            child_path = f"{path}.{key}"
            child_start = self._find_key_position(full_content, start_pos, key)
            self._chunk_value(val, full_content, child_path, depth + 1, child_start, chunks)

    def _chunk_array(
        self,
        arr: list[Any],
        full_content: str,
        path: str,
        depth: int,
        start_pos: int,
        chunks: list[Chunk],
    ) -> None:
        """Chunk a JSON array.

        Args:
            arr: JSON array.
            full_content: Original JSON string.
            path: Current path.
            depth: Current depth.
            start_pos: Character position.
            chunks: Output list.
        """
        if depth >= self.max_depth or len(arr) == 0:
            chunk_content = self._extract_json_segment(full_content, start_pos)
            token_count = self._tokenizer.count(chunk_content)

            chunks.append(
                Chunk(
                    id=Chunk.compute_id(chunk_content),
                    content=chunk_content,
                    format=ChunkFormat.JSON,
                    chunk_type=ChunkType.JSON_ARRAY,
                    token_count=token_count,
                    start_pos=start_pos,
                    dependencies=self._get_parent_dependencies(path),
                    metadata={"path": path, "depth": depth, "length": len(arr)},
                )
            )
            return

        for i, item in enumerate(arr):
            child_path = f"{path}[{i}]"
            child_start = self._find_array_item_position(full_content, start_pos, i)
            self._chunk_value(item, full_content, child_path, depth + 1, child_start, chunks)

    def _chunk_leaf(
        self,
        value: Any,
        full_content: str,
        path: str,
        depth: int,
        start_pos: int,
        chunks: list[Chunk],
    ) -> None:
        """Chunk a primitive JSON value.

        Args:
            value: Primitive JSON value.
            full_content: Original JSON string.
            path: Current path.
            depth: Current depth.
            start_pos: Character position.
            chunks: Output list.
        """
        import json

        chunk_content = self._extract_json_segment(full_content, start_pos)
        token_count = self._tokenizer.count(chunk_content)

        chunks.append(
            Chunk(
                id=Chunk.compute_id(chunk_content),
                content=chunk_content,
                format=ChunkFormat.JSON,
                chunk_type=ChunkType.JSON_LEAF,
                token_count=token_count,
                start_pos=start_pos,
                dependencies=self._get_parent_dependencies(path),
                metadata={
                    "path": path,
                    "depth": depth,
                    "value_type": type(value).__name__,
                },
            )
        )

    def _extract_json_segment(self, content: str, start_pos: int) -> str:
        """Extract a JSON segment starting from a position.

        Args:
            content: Full JSON string.
            start_pos: Starting position.

        Returns:
            JSON segment string.
        """
        depth = 0
        in_string = False
        escape = False
        start = start_pos
        end = start_pos

        while end < len(content):
            char = content[end]

            if escape:
                escape = False
                end += 1
                continue

            if char == "\\":
                escape = True
                end += 1
                continue

            if char == '"':
                in_string = not in_string
                end += 1
                continue

            if in_string:
                end += 1
                continue

            if char in "{[{":
                depth += 1
            elif char in "]}":
                depth -= 1

            end += 1

            if depth == 0 and char in "]}":
                break

        return content[start:end]

    def _find_key_position(
        self, content: str, start_pos: int, key: str
    ) -> int:
        """Find the position of a key in JSON string.

        Args:
            content: Full JSON string.
            start_pos: Position to search from.
            key: Key to find.

        Returns:
            Character position of the key.
        """
        import json

        key_str = f'"{key}"'
        pos = content.find(key_str, start_pos)
        if pos == -1:
            return start_pos
        return pos

    def _find_array_item_position(
        self, content: str, start_pos: int, index: int
    ) -> int:
        """Find position of array item by index.

        Args:
            content: Full JSON string.
            start_pos: Position of array start.
            index: Item index.

        Returns:
            Character position of the item.
        """
        depth = 0
        in_string = False
        escape = False
        current_index = 0
        pos = start_pos

        while pos < len(content):
            char = content[pos]

            if escape:
                escape = False
                pos += 1
                continue

            if char == "\\":
                escape = True
                pos += 1
                continue

            if char == '"':
                in_string = not in_string
                pos += 1
                continue

            if in_string:
                pos += 1
                continue

            if char in "[{":
                depth += 1
                pos += 1
                continue

            if char in "]}":
                depth -= 1
                pos += 1
                continue

            if depth == 1 and char == ",":
                current_index += 1
                pos += 1
                continue

            if depth == 1 and current_index == index:
                if char in " \t\n":
                    pos += 1
                    continue
                return pos

            if depth == 0 and char == "]":
                return pos

            pos += 1

        return start_pos

    def _get_parent_dependencies(self, path: str) -> tuple[str, ...]:
        """Get parent chunk IDs from a path.

        Args:
            path: Dot-notation path like "root.users[0].name".

        Returns:
            Tuple of parent chunk IDs.
        """
        parts = path.split(".")
        deps: list[str] = []

        for i in range(len(parts) - 1):
            parent_path = ".".join(parts[: i + 1])
            parent_path = parent_path.rstrip(".[0-9]")
            if parent_path:
                deps.append(parent_path)

        return tuple(deps)

    def _sort_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Sort chunks by start position.

        Args:
            chunks: Unsorted chunks.

        Returns:
            Chunks sorted by start_pos.
        """
        return sorted(chunks, key=lambda c: c.start_pos)
