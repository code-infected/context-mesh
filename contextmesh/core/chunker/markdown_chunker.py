"""Heading-based Markdown chunker.

Segments Markdown into sections at heading boundaries, keeping fenced
code blocks whole (a heading inside a code fence is not a boundary).
Large sections are split at sub-heading boundaries first, then by
paragraph groups. Content before the first heading becomes a preamble
chunk.

Architecture:
    Markdown text -> fence-aware line scan -> heading boundaries ->
    section chunks (split further if oversized)
"""

from __future__ import annotations

import re
from typing import ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkFormat,
    ChunkType,
)
from contextmesh.core.tokenizer import TokenCounter

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^(```+|~~~+)")


class MarkdownChunker(ChunkerBase):
    """Heading-based Markdown chunker.

    Attributes:
        max_chunk_tokens: Sections above this size are split further.
        min_chunk_tokens: Adjacent tiny sections are merged.

    Example:
        >>> chunker = MarkdownChunker()
        >>> chunks = chunker.chunk("# Title\\n\\nintro\\n\\n## Part\\n\\nbody")
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.MARKDOWN

    def __init__(
        self,
        max_chunk_tokens: int = 400,
        min_chunk_tokens: int = 20,
    ) -> None:
        """Initialize markdown chunker.

        Args:
            max_chunk_tokens: Split sections above this size.
            min_chunk_tokens: Merge sections below this size.
        """
        self.max_chunk_tokens = max_chunk_tokens
        self.min_chunk_tokens = min_chunk_tokens
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment Markdown into section chunks.

        Args:
            content: Markdown text.

        Returns:
            List of Chunks in document order.
        """
        if not content or not content.strip():
            return []

        sections = self._split_sections(content)
        sections = self._merge_small(sections)

        chunks: list[Chunk] = []
        for start_pos, heading, text in sections:
            token_count = self._tokenizer.count(text)
            if token_count > self.max_chunk_tokens:
                chunks.extend(self._split_large(start_pos, heading, text))
            else:
                chunks.append(self._make_chunk(start_pos, heading, text, token_count))
        return chunks

    def _split_sections(self, content: str) -> list[tuple[int, str | None, str]]:
        """Split at heading boundaries, ignoring headings inside fences.

        Returns:
            List of (start_offset, heading_title_or_None, section_text).
        """
        lines = content.split("\n")
        sections: list[tuple[int, str | None, str]] = []
        current: list[str] = []
        current_start = 0
        current_heading: str | None = None
        offset = 0
        in_fence = False
        fence_marker = ""

        def flush(next_start: int) -> None:
            nonlocal current, current_start, current_heading
            text = "\n".join(current)
            if text.strip():
                sections.append((current_start, current_heading, text))
            current = []
            current_start = next_start

        for line in lines:
            if in_fence:
                if line.strip().startswith(fence_marker):
                    in_fence = False
            else:
                fence_match = _FENCE_RE.match(line.strip())
                if fence_match:
                    in_fence = True
                    fence_marker = fence_match.group(1)[: 3]
                else:
                    heading = _HEADING_RE.match(line)
                    if heading and current:
                        flush(offset)
                        current_heading = heading.group(2).strip()
                    elif heading:
                        current_heading = heading.group(2).strip()

            current.append(line)
            offset += len(line) + 1

        flush(offset)
        return sections

    def _merge_small(
        self, sections: list[tuple[int, str | None, str]]
    ) -> list[tuple[int, str | None, str]]:
        """Merge adjacent sections below the minimum size."""
        if not sections:
            return []

        merged: list[tuple[int, str | None, str]] = []
        for section in sections:
            if merged:
                prev_start, prev_heading, prev_text = merged[-1]
                if self._tokenizer.count(prev_text) < self.min_chunk_tokens:
                    merged[-1] = (prev_start, prev_heading, prev_text + "\n" + section[2])
                    continue
            merged.append(section)
        return merged

    def _split_large(
        self, start_pos: int, heading: str | None, text: str
    ) -> list[Chunk]:
        """Split an oversized section by blank-line paragraph groups."""
        paragraphs = re.split(r"\n{2,}", text)
        chunks: list[Chunk] = []
        buffer: list[str] = []
        buffer_tokens = 0
        pos = start_pos
        part = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens, part
            if not buffer:
                return
            body = "\n\n".join(buffer)
            chunks.append(
                self._make_chunk(
                    pos + part, heading, body, self._tokenizer.count(body), part=part
                )
            )
            part += 1
            buffer = []
            buffer_tokens = 0

        for paragraph in paragraphs:
            tokens = self._tokenizer.count(paragraph)
            if buffer and buffer_tokens + tokens > self.max_chunk_tokens:
                flush()
            buffer.append(paragraph)
            buffer_tokens += tokens
        flush()
        return chunks

    def _make_chunk(
        self,
        start_pos: int,
        heading: str | None,
        text: str,
        token_count: int,
        part: int | None = None,
    ) -> Chunk:
        is_code = text.lstrip().startswith(("```", "~~~"))
        if heading is None:
            chunk_type = ChunkType.MARKDOWN_PREAMBLE
        elif is_code:
            chunk_type = ChunkType.MARKDOWN_CODE_BLOCK
        else:
            chunk_type = ChunkType.MARKDOWN_SECTION

        metadata: dict = {"heading": heading}
        if part is not None:
            metadata["part"] = part

        return Chunk(
            id=Chunk.compute_id(text),
            content=text,
            format=ChunkFormat.MARKDOWN,
            chunk_type=chunk_type,
            token_count=token_count,
            start_pos=start_pos,
            dependencies=(),
            metadata=metadata,
        )
