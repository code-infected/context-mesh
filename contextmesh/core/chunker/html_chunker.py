"""HTML DOM-based chunker.

Segments HTML into semantic sections using lxml DOM parsing.
Chunks are defined at semantic tag boundaries (article, section, main, nav, aside)
and major div blocks with meaningful class names.

Architecture:
    HTML string -> lxml parse -> DOM traversal ->
    semantic section identification -> section chunks
"""

from __future__ import annotations

from typing import ClassVar

from lxml import etree, html

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkerError,
    ChunkFormat,
    ChunkType,
)
from contextmesh.core.tokenizer import TokenCounter

SEMANTIC_TAGS: frozenset[str] = frozenset([
    "article", "section", "main", "nav", "aside", "header", "footer",
    "div", "blockquote", "ul", "ol", "table", "figure"
])


class HTMLChunker(ChunkerBase):
    """DOM-based HTML semantic section chunker.

    Uses lxml to parse HTML and identifies semantic sections based on
    tag names and class attributes. Script and style tags are stripped
    before processing.

    Attributes:
        semantic_tags: HTML tags that define semantic boundaries.
        min_chunk_tokens: Minimum tokens for a valid chunk.

    Example:
        >>> chunker = HTMLChunker(semantic_tags=["article", "section", "main"])
        >>> html_content = '<article><p>Hello</p></article>'
        >>> chunks = chunker.chunk(html_content)
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.HTML
    semantic_tags: ClassVar[frozenset[str]] = frozenset([
        "article", "section", "main", "nav", "aside"
    ])

    def __init__(
        self,
        semantic_tags: list[str] | None = None,
        min_chunk_tokens: int = 50,
    ) -> None:
        """Initialize HTML chunker.

        Args:
            semantic_tags: Tags to treat as semantic boundaries.
            min_chunk_tokens: Minimum tokens for a chunk.
        """
        if semantic_tags:
            self.semantic_tags = frozenset(semantic_tags)
        self.min_chunk_tokens = min_chunk_tokens
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment HTML into semantic sections.

        Args:
            content: HTML string to chunk.

        Returns:
            List of semantic section chunks.

        Raises:
            ChunkerError: If HTML parsing fails.
        """
        try:
            doc = html.fromstring(content)
        except Exception as e:
            raise ChunkerError(f"Failed to parse HTML: {e}", format=self.format) from e

        self._strip_scripts_and_styles(doc)

        chunks: list[Chunk] = []
        self._process_node(doc, chunks, depth=0)

        return self._sort_and_validate(chunks)

    def _strip_scripts_and_styles(self, doc: etree._Element) -> None:
        """Remove script and style elements.

        Args:
            doc: lxml document element.
        """
        for element in doc.iter("script", "style", "noscript"):
            element.getparent().remove(element) if element.getparent() is not None else None

    def _process_node(
        self,
        node: etree._Element,
        chunks: list[Chunk],
        depth: int,
    ) -> None:
        """Recursively process DOM nodes.

        Args:
            node: Current DOM node.
            chunks: Output chunks list.
            depth: Current traversal depth.
        """
        if not isinstance(node.tag, str):
            for child in node:
                self._process_node(child, chunks, depth + 1)
            return

        tag = node.tag.lower()

        if tag in self.semantic_tags:
            chunk = self._node_to_chunk(node, tag)
            if chunk and chunk.token_count >= self.min_chunk_tokens:
                chunks.append(chunk)
                return

        meaningful_class = self._has_meaningful_class(node)
        if tag == "div" and meaningful_class:
            chunk = self._node_to_chunk(node, "div")
            if chunk and chunk.token_count >= self.min_chunk_tokens:
                chunks.append(chunk)
                return

        for child in node:
            self._process_node(child, chunks, depth + 1)

    def _has_meaningful_class(self, node: etree._Element) -> bool:
        """Check if a div has a meaningful class name.

        Filters out common framework classes like "container", "wrapper",
        "row", "col" that don't indicate semantic content.

        Args:
            node: DOM element to check.

        Returns:
            True if class name is meaningful.
        """
        class_attr = node.get("class", "")
        if not class_attr:
            return False

        meaningless = frozenset([
            "container", "wrapper", "row", "col", "col-", "row-",
            "span", "grid", "flex", "d-", "p-", "m-", "mt-", "mb-",
            "ml-", "mr-", "px-", "py-", "text-", "bg-", "border",
            "shadow", "rounded", "shadow-sm", "shadow-lg"
        ])

        classes = class_attr.lower().split()
        for cls in classes:
            if cls not in meaningless and not any(cls.startswith(p) for p in meaningless):
                return True

        return False

    def _node_to_chunk(self, node: etree._Element, semantic_type: str) -> Chunk | None:
        """Convert a DOM node to a Chunk.

        Args:
            node: DOM element.
            semantic_type: Semantic type tag name.

        Returns:
            Chunk or None if node is empty.
        """
        try:
            content = html.tostring(node, encoding="unicode", method="html")
        except Exception:
            return None

        content = content.strip()
        if not content:
            return None

        chunk_type = self._semantic_type_to_chunk_type(semantic_type)

        return Chunk(
            id=Chunk.compute_id(content),
            content=content,
            format=ChunkFormat.HTML,
            chunk_type=chunk_type,
            token_count=self._tokenizer.count(content),
            start_pos=0,
            dependencies=(),
            metadata={
                "semantic_tag": semantic_type,
                "class": node.get("class", ""),
                "id": node.get("id", ""),
            },
        )

    def _semantic_type_to_chunk_type(self, tag: str) -> ChunkType:
        """Map HTML tag to ChunkType.

        Args:
            tag: HTML tag name.

        Returns:
            Corresponding ChunkType.
        """
        mapping = {
            "article": ChunkType.HTML_ARTICLE,
            "section": ChunkType.HTML_SECTION,
            "main": ChunkType.HTML_MAIN,
            "nav": ChunkType.HTML_NAV,
            "aside": ChunkType.HTML_ASIDE,
            "div": ChunkType.HTML_DIV,
        }
        return mapping.get(tag, ChunkType.HTML_DIV)

    def _sort_and_validate(self, chunks: list[Chunk]) -> list[Chunk]:
        """Sort chunks and validate minimums.

        Args:
            chunks: Unsorted chunks.

        Returns:
            Valid chunks sorted by position.
        """
        valid = [c for c in chunks if c.token_count >= self.min_chunk_tokens]
        return sorted(valid, key=lambda c: c.start_pos)
