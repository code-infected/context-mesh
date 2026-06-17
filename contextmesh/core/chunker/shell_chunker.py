"""Shell output chunker.

Segments shell command output into command-output pairs.
Each '$ command' line followed by its output is one chunk.
For outputs without prompts, uses blank-line-separated paragraphs.

Architecture:
    Shell text -> prompt detection -> command-output pairing ->
    blank-line paragraph grouping -> chunks
"""

from __future__ import annotations

import re
from typing import ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkFormat,
    ChunkType,
    ChunkerBase,
    ChunkerError,
)
from contextmesh.core.tokenizer import TokenCounter


PROMPT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\$\s+", re.MULTILINE),
    re.compile(r"^>\s+", re.MULTILINE),
    re.compile(r"^%\s+", re.MULTILINE),
    re.compile(r"^#\s+", re.MULTILINE),
    re.compile(r"\$ ", re.MULTILINE),
    re.compile(r"sh\$ ", re.MULTILINE),
    re.compile(r"bash\$ ", re.MULTILINE),
]


class ShellChunker(ChunkerBase):
    """Command-output pair shell output chunker.

    Segments shell output by identifying command prompts and grouping
    each command with its output. For outputs without explicit prompts,
    groups by blank-line-separated paragraphs with a minimum line threshold.

    Attributes:
        min_lines_per_chunk: Minimum lines for a paragraph chunk.
        blank_line_separator: Whether blank lines separate chunks.

    Example:
        >>> chunker = ShellChunker(min_lines_per_chunk=3)
        >>> output = '''$ ls -la
        ... total 100
        ... drwxr-xr-x  5 user staff 160 Jan 15 10:00 .
        ... $ echo hello
        ... hello'''
        >>> chunks = chunker.chunk(output)
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.SHELL

    def __init__(
        self,
        min_lines_per_chunk: int = 3,
        blank_line_separator: bool = True,
    ) -> None:
        """Initialize shell chunker.

        Args:
            min_lines_per_chunk: Minimum lines for paragraph chunk.
            blank_line_separator: Use blank lines as chunk boundaries.
        """
        self.min_lines_per_chunk = min_lines_per_chunk
        self.blank_line_separator = blank_line_separator
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment shell output into command-output pairs.

        Args:
            content: Shell output text to chunk.

        Returns:
            List of command-output chunks.

        Raises:
            ChunkerError: If shell parsing fails.
        """
        if not content.strip():
            return []

        if self._has_prompts(content):
            chunks = self._chunk_by_prompts(content)
        elif self.blank_line_separator:
            chunks = self._chunk_by_paragraphs(content)
        else:
            chunks = self._chunk_by_lines(content)

        return self._sort_and_assign_positions(chunks)

    def _has_prompts(self, content: str) -> bool:
        """Check if content has command prompts.

        Args:
            content: Shell output to check.

        Returns:
            True if prompts detected.
        """
        for pattern in PROMPT_PATTERNS:
            if pattern.search(content):
                return True
        return False

    def _chunk_by_prompts(self, content: str) -> list[Chunk]:
        """Chunk by command prompt lines.

        Args:
            content: Shell output with prompts.

        Returns:
            List of command-output chunks.
        """
        lines = content.split("\n")
        chunks: list[Chunk] = []
        current_command: str | None = None
        current_output_lines: list[str] = []
        current_start_line = 0

        for i, line in enumerate(lines):
            is_prompt = False
            for pattern in PROMPT_PATTERNS:
                if pattern.match(line):
                    is_prompt = True
                    break

            if is_prompt:
                if current_command is not None:
                    chunk = self._create_command_chunk(
                        current_command, current_output_lines, current_start_line
                    )
                    if chunk:
                        chunks.append(chunk)

                current_command = line
                current_output_lines = []
                current_start_line = i
            else:
                current_output_lines.append(line)

        if current_command is not None:
            chunk = self._create_command_chunk(
                current_command, current_output_lines, current_start_line
            )
            if chunk:
                chunks.append(chunk)

        return chunks

    def _create_command_chunk(
        self, command: str, output_lines: list[str], start_line: int
    ) -> Chunk | None:
        """Create a chunk from command and output.

        Args:
            command: Command line.
            output_lines: Output lines following command.
            start_line: Starting line number.

        Returns:
            Chunk or None if too small.
        """
        if len(output_lines) < self.min_lines_per_chunk and not output_lines:
            return None

        content = command + "\n" + "\n".join(output_lines)

        return Chunk(
            id=Chunk.compute_id(content),
            content=content,
            format=ChunkFormat.SHELL,
            chunk_type=ChunkType.SHELL_COMMAND,
            token_count=self._tokenizer.count(content),
            start_pos=0,
            dependencies=(),
            metadata={
                "command": command.strip(),
                "num_output_lines": len(output_lines),
                "start_line": start_line,
            },
        )

    def _chunk_by_paragraphs(self, content: str) -> list[Chunk]:
        """Chunk by blank-line-separated paragraphs.

        Args:
            content: Shell output without prompts.

        Returns:
            List of paragraph chunks.
        """
        paragraphs = content.split("\n\n")
        chunks: list[Chunk] = []

        for para in paragraphs:
            lines = para.strip().split("\n")
            if len(lines) >= self.min_lines_per_chunk:
                chunks.append(
                    Chunk(
                        id=Chunk.compute_id(para),
                        content=para,
                        format=ChunkFormat.SHELL,
                        chunk_type=ChunkType.SHELL_OUTPUT,
                        token_count=self._tokenizer.count(para),
                        start_pos=0,
                        dependencies=(),
                        metadata={"num_lines": len(lines)},
                    )
                )

        return chunks

    def _chunk_by_lines(self, content: str) -> list[Chunk]:
        """Chunk by individual lines (fallback).

        Args:
            content: Shell output.

        Returns:
            List of line-based chunks.
        """
        lines = content.split("\n")
        chunks: list[Chunk] = []

        for i in range(0, len(lines), self.min_lines_per_chunk):
            window = lines[i : i + self.min_lines_per_chunk]
            content_chunk = "\n".join(window)

            chunks.append(
                Chunk(
                    id=Chunk.compute_id(content_chunk),
                    content=content_chunk,
                    format=ChunkFormat.SHELL,
                    chunk_type=ChunkType.SHELL_OUTPUT,
                    token_count=self._tokenizer.count(content_chunk),
                    start_pos=0,
                    dependencies=(),
                    metadata={
                        "start_line": i,
                        "num_lines": len(window),
                    },
                )
            )

        return chunks

    def _sort_and_assign_positions(self, chunks: list[Chunk]) -> list[Chunk]:
        """Sort chunks and assign byte positions.

        Args:
            chunks: Unsorted chunks.

        Returns:
            Chunks sorted by start position.
        """
        sorted_chunks = sorted(chunks, key=lambda c: c.metadata.get("start_line", 0))

        offset = 0
        result: list[Chunk] = []

        for chunk in sorted_chunks:
            new_chunk = Chunk(
                id=chunk.id,
                content=chunk.content,
                format=chunk.format,
                chunk_type=chunk.chunk_type,
                token_count=chunk.token_count,
                start_pos=offset,
                dependencies=chunk.dependencies,
                metadata=chunk.metadata,
            )
            result.append(new_chunk)
            offset += len(chunk.content) + 1

        return result
