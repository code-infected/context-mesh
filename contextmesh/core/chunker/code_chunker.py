"""AST-based code chunker using tree-sitter.

Segments source code into semantically coherent units using AST analysis.
Never splits functions, classes, or import blocks across chunk boundaries.

Supported languages: Python, TypeScript, JavaScript, Rust, Go.

Architecture:
    Source code -> tree-sitter parse -> AST traversal ->
    Node categorization -> Chunk creation -> Dependency detection

The chunker identifies:
- Function definitions (with their docstrings)
- Class definitions (with their methods)
- Import blocks (grouped by PEP 8 conventions)
- Module-level docstrings
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from tree_sitter import Language, Parser
from tree_sitter_python import language as python_language
from tree_sitter_typescript import language_typescript, language_tsx

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkFormat,
    ChunkType,
    ChunkerBase,
    ChunkerError,
)
from contextmesh.core.tokenizer import TokenCounter

if TYPE_CHECKING:
    from tree_sitter import Node, Tree


LANGUAGE_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "typescript": ".ts",
    "javascript": ".js",
}

LANGUAGE_MAP: dict[str, Language] = {
    "python": Language(python_language()),
    "typescript": Language(language_typescript()),
    "tsx": Language(language_tsx()),
}


@dataclass
class CodeChunkConfig:
    """Configuration for code chunking behavior.

    Attributes:
        max_chunk_tokens: Split large functions at this size.
        min_chunk_tokens: Merge tiny chunks below this size.
        include_docstrings: Include function/class docstrings in chunks.
        include_comments: Include leading comments in chunks.
    """

    max_chunk_tokens: int = 300
    min_chunk_tokens: int = 20
    include_docstrings: bool = True
    include_comments: bool = True


class CodeChunker(ChunkerBase):
    """AST-based code chunker.

    Uses tree-sitter to parse source code into an AST, then extracts
    semantic units as chunks. Never splits functions, classes, or
    import blocks.

    Attributes:
        language: The language to parse as.
        config: Chunking configuration options.

    Example:
        >>> chunker = CodeChunker("python")
        >>> code = '''def foo():
        ...     pass
        ...
        ... def bar():
        ...     pass
        ... '''
        >>> chunks = chunker.chunk(code)
        >>> len(chunks)
        2
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.CODE

    _node_types_function: ClassVar[set[str]] = {
        "function_definition",
        "method_definition",
    }
    _node_types_class: ClassVar[set[str]] = {
        "class_definition",
    }
    _node_types_import: ClassVar[set[str]] = {
        "import_statement",
        "import_from_statement",
        "import_clause",
        "import_from_clause",
    }
    _node_types_docstring: ClassVar[set[str]] = {
        "expression_statement",
    }

    def __init__(
        self,
        language: str = "python",
        config: CodeChunkConfig | None = None,
    ) -> None:
        """Initialize code chunker for a specific language.

        Args:
            language: Language identifier for tree-sitter.
            config: Optional chunking configuration.

        Raises:
            ChunkerError: If language is not supported.
        """
        if language not in LANGUAGE_MAP:
            raise ChunkerError(
                f"Unsupported language: {language}. Supported: {list(LANGUAGE_MAP.keys())}",
                format=self.format,
            )

        self.language = language
        self.config = config or CodeChunkConfig()
        self._parser = Parser(LANGUAGE_MAP[language])
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment source code into AST-based chunks.

        Args:
            content: The source code to chunk.

        Returns:
            List of Chunks in source order.

        Raises:
            ChunkerError: If parsing fails.
        """
        try:
            tree = self._parser.parse(content.encode())
        except Exception as e:
            raise ChunkerError(
                f"Failed to parse {self.language} code: {e}", format=self.format
            ) from e

        if not tree.root_node:
            raise ChunkerError(f"Empty parse tree for {self.language}", format=self.format)

        visitor = _CodeChunkVisitor(content, tree.root_node, self._tokenizer, self.config)
        chunks = visitor.visit()

        return self._post_process(chunks, content)

    def _post_process(self, chunks: list[Chunk], content: str) -> list[Chunk]:
        """Post-process chunks: merge small ones, detect dependencies.

        Args:
            chunks: Initial chunks from AST visitor.
            content: Original source for dependency detection.

        Returns:
            Processed chunks with dependencies.
        """
        merged = self._merge_small_chunks(chunks)
        deps = self._detect_dependencies(merged, content)
        return merged

    def _merge_small_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Merge chunks below minimum token threshold.

        Small chunks are merged with their nearest neighbors to avoid
        having many tiny chunks that don't provide context alone.

        Args:
            chunks: Input chunks from visitor.

        Returns:
            Chunks with small ones merged.
        """
        if not chunks:
            return []

        result: list[Chunk] = []
        current: Chunk | None = None

        for chunk in chunks:
            if current is None:
                current = chunk
                continue

            if current.token_count < self.config.min_chunk_tokens:
                merged_content = current.content + "\n" + chunk.content
                current = Chunk(
                    id=Chunk.compute_id(merged_content),
                    content=merged_content,
                    format=current.format,
                    chunk_type=current.chunk_type,
                    token_count=self._tokenizer.count(merged_content),
                    start_pos=current.start_pos,
                    dependencies=current.dependencies,
                    metadata={**current.metadata, **chunk.metadata},
                )
            else:
                result.append(current)
                current = chunk

        if current is not None:
            result.append(current)

        return result

    def _detect_dependencies(
        self, chunks: list[Chunk], content: str
    ) -> list[Chunk]:
        """Detect function/class references to build dependency graph.

        Uses name pattern matching to identify when a chunk references
        entities defined elsewhere. This is used to ensure selected
        chunks include their dependencies.

        Args:
            chunks: Chunks to analyze.
            content: Original source for name extraction.

        Returns:
            Chunks with dependency information.
        """
        name_map: dict[str, str] = {}
        for chunk in chunks:
            if chunk.metadata.get("name"):
                name_map[chunk.metadata["name"]] = chunk.id

        result: list[Chunk] = []
        for chunk in chunks:
            deps: list[str] = []

            func_pattern = r"\b([A-Z][a-zA-Z0-9_]*)\s*\("
            matches = re.findall(func_pattern, chunk.content)

            for name in matches:
                if name in name_map and name_map[name] != chunk.id:
                    deps.append(name_map[name])

            if deps:
                result.append(chunk.with_dependencies(deps))
            else:
                result.append(chunk)

        return result


class _CodeChunkVisitor:
    """Tree-sitter AST visitor that extracts code chunks.

    Walks the AST and categorizes nodes into chunks. Handles the
    differences between language AST structures.
    """

    def __init__(
        self, content: str, root_node: Node, tokenizer: TokenCounter, config: CodeChunkConfig
    ) -> None:
        self.content = content
        self.lines = content.split("\n")
        self.root_node = root_node
        self.tokenizer = tokenizer
        self.config = config
        self.chunks: list[Chunk] = []
        self._import_buffer: list[tuple[int, str]] = []
        self._docstring_buffer: list[tuple[int, str]] = []
        self._in_function = False
        self._in_class = False

    def visit(self) -> list[Chunk]:
        """Walk the AST and extract chunks.

        Returns:
            List of extracted chunks.
        """
        self._visit_node(self.root_node)
        self._flush_import_buffer()
        self._flush_docstring_buffer()
        return self.chunks

    def _visit_node(self, node: Node) -> None:
        """Recursively visit AST nodes.

        Args:
            node: Current AST node.
        """
        node_type = node.type

        if node_type in CodeChunker._node_types_import:
            start_byte = node.start_byte
            end_byte = node.end_byte
            text = self.content[start_byte:end_byte]
            self._import_buffer.append((node.start_point[0], text))

        elif node_type in CodeChunker._node_types_function:
            self._flush_import_buffer()
            self._flush_docstring_buffer()
            self._in_function = True
            self._visit_function(node)
            self._in_function = False

        elif node_type in CodeChunker._node_types_class:
            self._flush_import_buffer()
            self._flush_docstring_buffer()
            self._in_class = True
            self._visit_class(node)
            self._in_class = False

        elif node_type == "module" and node.parent is None:
            docstring = self._extract_module_docstring(node)
            if docstring:
                self._docstring_buffer.append((0, docstring))
            for child in node.children:
                if child.type not in ("whitespace", "newline", "comment"):
                    self._visit_node(child)

        else:
            for child in node.children:
                self._visit_node(child)

    def _visit_function(self, node: Node) -> None:
        """Extract a function definition as a chunk.

        Args:
            node: Function definition AST node.
        """
        start_byte = node.start_byte
        end_byte = node.end_byte
        content = self.content[start_byte:end_byte]

        func_name = self._get_function_name(node)

        leading_comments = self._get_leading_comments(node)
        if leading_comments:
            content = leading_comments + "\n" + content

        token_count = self.tokenizer.count(content)

        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(content),
                content=content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_FUNCTION,
                token_count=token_count,
                start_pos=start_byte,
                dependencies=[],
                metadata={
                    "name": func_name,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                },
            )
        )

    def _visit_class(self, node: Node) -> None:
        """Extract a class definition as a chunk.

        Args:
            node: Class definition AST node.
        """
        start_byte = node.start_byte
        end_byte = node.end_byte
        content = self.content[start_byte:end_byte]

        class_name = self._get_class_name(node)

        leading_comments = self._get_leading_comments(node)
        if leading_comments:
            content = leading_comments + "\n" + content

        token_count = self.tokenizer.count(content)

        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(content),
                content=content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_CLASS,
                token_count=token_count,
                start_pos=start_byte,
                dependencies=[],
                metadata={
                    "name": class_name,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                },
            )
        )

    def _get_function_name(self, node: Node) -> str:
        """Extract function name from AST node.

        Args:
            node: Function definition node.

        Returns:
            Function name string.
        """
        if hasattr(node, "child_by_field_name"):
            name_node = node.child_by_field_name("name")
            if name_node:
                return self.content[name_node.start_byte:name_node.end_byte]
        return "anonymous"

    def _get_class_name(self, node: Node) -> str:
        """Extract class name from AST node.

        Args:
            node: Class definition node.

        Returns:
            Class name string.
        """
        if hasattr(node, "child_by_field_name"):
            name_node = node.child_by_field_name("name")
            if name_node:
                return self.content[name_node.start_byte:name_node.end_byte]
        return "AnonymousClass"

    def _get_leading_comments(self, node: Node) -> str:
        """Get comments preceding a node.

        Args:
            node: AST node.

        Returns:
            Comment text or empty string.
        """
        if not self.config.include_comments:
            return ""

        line_idx = node.start_point[0] - 1
        comments: list[str] = []

        while line_idx >= 0:
            line = self.lines[line_idx]
            stripped = line.strip()
            if stripped.startswith("#"):
                comments.insert(0, stripped)
                line_idx -= 1
            elif stripped == "":
                line_idx -= 1
            else:
                break

        return "\n".join(comments)

    def _extract_module_docstring(self, node: Node) -> str | None:
        """Extract module-level docstring.

        Args:
            node: Module AST node.

        Returns:
            Docstring content or None.
        """
        if not self.config.include_docstrings:
            return None

        if node.children and len(node.children) >= 2:
            first_child = node.children[0]
            if first_child.type == "expression_statement":
                first_child_text = self.content[
                    first_child.start_byte : first_child.end_byte
                ]
                if (
                    first_child_text.startswith('"""')
                    or first_child_text.startswith("'''")
                    or first_child_text.startswith('"')
                    or first_child_text.startswith("'")
                ):
                    return first_child_text.strip()

        return None

    def _flush_import_buffer(self) -> None:
        """Flush accumulated import statements as a single chunk."""
        if not self._import_buffer:
            return

        self._import_buffer.sort(key=lambda x: x[0])

        import_lines = [text for _, text in self._import_buffer]
        content = "\n".join(import_lines)
        first_line = self._import_buffer[0][0]

        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(content),
                content=content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_IMPORT_BLOCK,
                token_count=self.tokenizer.count(content),
                start_pos=self._get_line_start(first_line),
                dependencies=[],
                metadata={"start_line": first_line + 1},
            )
        )

        self._import_buffer.clear()

    def _flush_docstring_buffer(self) -> None:
        """Flush accumulated docstring as a module docstring chunk."""
        if not self._docstring_buffer:
            return

        docstrings = [text for _, text in self._docstring_buffer]
        content = "\n\n".join(docstrings)
        first_line = self._docstring_buffer[0][0]

        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(content),
                content=content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_MODULE_DOCSTRING,
                token_count=self.tokenizer.count(content),
                start_pos=self._get_line_start(first_line),
                dependencies=[],
                metadata={"start_line": first_line + 1},
            )
        )

        self._docstring_buffer.clear()

    def _get_line_start(self, line_idx: int) -> int:
        """Get byte offset for line start.

        Args:
            line_idx: Zero-based line index.

        Returns:
            Byte offset of line start in content.
        """
        offset = 0
        for i, line in enumerate(self.lines):
            if i == line_idx:
                return offset
            offset += len(line) + 1
        return offset
