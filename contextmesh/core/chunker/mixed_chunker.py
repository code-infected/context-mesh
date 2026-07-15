"""Mixed-format detection and dispatching chunker.

Detects format boundaries within a single tool response and
dispatches to appropriate sub-chunkers. Handles the common
case where a coding agent's tool output mixes multiple formats.

Architecture:
    Mixed content -> format boundary detection ->
    format-specific sub-chunking -> unified chunk list

Detected formats:
    - JSON (starts with { or [)
    - Python/JS code (def, function, class, import)
    - HTML (<html, <div, <article)
    - Shell ($, prompt patterns)
    - Log lines (timestamps, log levels)
"""

from __future__ import annotations

import re
from typing import ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkFormat,
)
from contextmesh.core.chunker.code_chunker import CodeChunker
from contextmesh.core.chunker.html_chunker import HTMLChunker
from contextmesh.core.chunker.json_chunker import JSONChunker
from contextmesh.core.chunker.log_chunker import LogChunker
from contextmesh.core.chunker.shell_chunker import ShellChunker

JSON_START = re.compile(r'^\s*[\[{]')
CODE_BLOCK = re.compile(
    r'^\s*(def\s+\w+|class\s+\w+|function\s+\w+|import\s+\w+|const\s+\w+|let\s+\w+|var\s+\w+)',
    re.MULTILINE
)
HTML_START = re.compile(r'^\s*<(html|div|article|section|main|nav|aside|p|span|ul|ol|li)', re.IGNORECASE)
SHELL_PROMPT = re.compile(r'^[$%#]\s+', re.MULTILINE)
LOG_TIMESTAMP = re.compile(
    r'^\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\[\d{4}-\d{2}-\d{2}',
    re.MULTILINE
)


class MixedChunker(ChunkerBase):
    """Mixed-format detection and dispatch chunker.

    Analyzes a tool response to detect format boundaries, then
    dispatches each section to the appropriate specialized chunker.
    Combines results into a unified chunk list preserving order.

    Attributes:
        sub_chunker_config: Configuration overrides for sub-chunkers.

    Example:
        >>> chunker = MixedChunker()
        >>> mixed = '''[{"id": 1}]\\n\\ndef foo(): pass'''
        >>> chunks = chunker.chunk(mixed)
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.TEXT

    def __init__(
        self,
        code_chunker: CodeChunker | None = None,
        json_chunker: JSONChunker | None = None,
        html_chunker: HTMLChunker | None = None,
        log_chunker: LogChunker | None = None,
        shell_chunker: ShellChunker | None = None,
    ) -> None:
        """Initialize mixed chunker with sub-chunkers.

        Args:
            code_chunker: Optional code chunker override.
            json_chunker: Optional JSON chunker override.
            html_chunker: Optional HTML chunker override.
            log_chunker: Optional log chunker override.
            shell_chunker: Optional shell chunker override.
        """
        self.code_chunker = code_chunker or CodeChunker("python")
        self.json_chunker = json_chunker or JSONChunker()
        self.html_chunker = html_chunker or HTMLChunker()
        self.log_chunker = log_chunker or LogChunker()
        self.shell_chunker = shell_chunker or ShellChunker()

    def chunk(self, content: str) -> list[Chunk]:
        """Detect formats and dispatch to sub-chunkers.

        Args:
            content: Mixed-format content to chunk.

        Returns:
            Unified list of chunks from all sub-chunkers.

        Raises:
            ChunkerError: If format detection or chunking fails.
        """
        if not content.strip():
            return []

        segments = self._detect_segments(content)

        if len(segments) == 1:
            return self._chunk_single_segment(content, segments[0])

        return self._dispatch_segments(segments, content)

    def _detect_segments(self, content: str) -> list[dict[str, str | int]]:
        """Detect format boundaries in content.

        Args:
            content: Content to analyze.

        Returns:
            List of segments with format and position.
        """
        segments: list[dict[str, str | int]] = []
        lines = content.split("\n")
        current_format: str | None = None
        segment_start = 0
        current_lines: list[str] = []

        for i, line in enumerate(lines):
            detected_format = self._detect_line_format(line)

            if detected_format != current_format:
                if current_lines:
                    segments.append({
                        "format": current_format or "text",
                        "start": segment_start,
                        "end": i,
                        "content": "\n".join(current_lines),
                    })

                current_format = detected_format
                segment_start = i
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            segments.append({
                "format": current_format or "text",
                "start": segment_start,
                "end": len(lines),
                "content": "\n".join(current_lines),
            })

        return segments

    def _detect_line_format(self, line: str) -> str:
        """Detect format of a single line.

        Args:
            line: Line to analyze.

        Returns:
            Format name string.
        """
        stripped = line.strip()

        if not stripped:
            return "text"

        if JSON_START.match(stripped):
            return "json"

        if HTML_START.match(stripped):
            return "html"

        if SHELL_PROMPT.match(stripped):
            return "shell"

        if LOG_TIMESTAMP.match(stripped):
            return "log"

        if CODE_BLOCK.match(stripped):
            return "code"

        return "text"

    def _chunk_single_segment(
        self, content: str, segment: dict[str, str | int]
    ) -> list[Chunk]:
        """Chunk content when only one format detected.

        Args:
            content: Full content.
            segment: Single detected segment.

        Returns:
            Chunks from appropriate sub-chunker.
        """
        fmt = segment["format"]
        segment_content = segment["content"]

        offset = content.index(segment_content)

        if fmt == "json":
            chunks = self.json_chunker.chunk(segment_content)
        elif fmt == "html":
            chunks = self.html_chunker.chunk(segment_content)
        elif fmt == "shell":
            chunks = self.shell_chunker.chunk(segment_content)
        elif fmt == "log":
            chunks = self.log_chunker.chunk(segment_content)
        elif fmt == "code":
            chunks = self.code_chunker.chunk(segment_content)
        else:
            chunks = [self._create_text_chunk(segment_content, offset)]

        return self._fix_positions(chunks, offset)

    def _dispatch_segments(
        self, segments: list[dict[str, str | int]], full_content: str
    ) -> list[Chunk]:
        """Dispatch each segment to appropriate chunker.

        Args:
            segments: Detected format segments.
            full_content: Original content for position calculation.

        Returns:
            Combined chunk list.
        """
        all_chunks: list[Chunk] = []

        for seg in segments:
            fmt = seg["format"]
            seg_content = seg["content"]
            start_pos = full_content.index(seg_content)

            if fmt == "json":
                chunks = self.json_chunker.chunk(seg_content)
            elif fmt == "html":
                chunks = self.html_chunker.chunk(seg_content)
            elif fmt == "shell":
                chunks = self.shell_chunker.chunk(seg_content)
            elif fmt == "log":
                chunks = self.log_chunker.chunk(seg_content)
            elif fmt == "code":
                chunks = self.code_chunker.chunk(seg_content)
            else:
                chunks = [self._create_text_chunk(seg_content, start_pos)]

            fixed = self._fix_positions(chunks, start_pos)
            all_chunks.extend(fixed)

        return self._sort_by_position(all_chunks)

    def _create_text_chunk(self, content: str, start_pos: int) -> Chunk:
        """Create a text chunk for unrecognized content.

        Args:
            content: Text content.
            start_pos: Position in original.

        Returns:
            Text chunk.
        """
        from contextmesh.core.chunker.base import ChunkFormat, ChunkType

        return Chunk(
            id=Chunk.compute_id(content),
            content=content,
            format=ChunkFormat.TEXT,
            chunk_type=ChunkType.TEXT_PARAGRAPH,
            token_count=self.json_chunker._tokenizer.count(content),
            start_pos=start_pos,
            dependencies=(),
            metadata={},
        )

    def _fix_positions(
        self, chunks: list[Chunk], base_offset: int
    ) -> list[Chunk]:
        """Fix chunk positions to be absolute within original content.

        Args:
            chunks: Chunks with relative positions.
            base_offset: Base offset to add.

        Returns:
            Chunks with fixed positions.
        """
        result: list[Chunk] = []
        for chunk in chunks:
            result.append(
                Chunk(
                    id=chunk.id,
                    content=chunk.content,
                    format=chunk.format,
                    chunk_type=chunk.chunk_type,
                    token_count=chunk.token_count,
                    start_pos=chunk.start_pos + base_offset,
                    dependencies=chunk.dependencies,
                    metadata=chunk.metadata,
                )
            )
        return result

    def _sort_by_position(self, chunks: list[Chunk]) -> list[Chunk]:
        """Sort chunks by start position.

        Args:
            chunks: Unsorted chunks.

        Returns:
            Position-sorted chunks.
        """
        return sorted(chunks, key=lambda c: c.start_pos)
