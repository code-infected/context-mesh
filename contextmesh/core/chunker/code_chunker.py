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

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkerError,
    ChunkFormat,
    ChunkType,
)
from contextmesh.core.tokenizer import TokenCounter

if TYPE_CHECKING:
    from tree_sitter import Node


LANGUAGE_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "typescript": ".ts",
    "tsx": ".tsx",
    "javascript": ".js",
    "rust": ".rs",
    "go": ".go",
}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(LANGUAGE_EXTENSIONS)

_LANGUAGE_CACHE: dict[str, Language] = {}


def language_for_path(path: str) -> str | None:
    """Map a file path to a supported language identifier.

    Args:
        path: File path (only the extension is inspected).

    Returns:
        Language identifier, or None if the extension is unknown.
    """
    lowered = path.lower()
    for lang, ext in LANGUAGE_EXTENSIONS.items():
        if lowered.endswith(ext):
            return lang
    return None


def _load_language(name: str) -> Language:
    """Load a tree-sitter Language, importing its grammar lazily.

    Grammars are optional installs; loading lazily keeps unused
    grammars out of the import path entirely.

    Args:
        name: Language identifier.

    Returns:
        The tree-sitter Language object.

    Raises:
        ChunkerError: If the language is unsupported or its grammar
            package is not installed.
    """
    cached = _LANGUAGE_CACHE.get(name)
    if cached is not None:
        return cached

    try:
        if name == "python":
            from tree_sitter_python import language as lang_fn
        elif name == "typescript":
            from tree_sitter_typescript import language_typescript as lang_fn
        elif name == "tsx":
            from tree_sitter_typescript import language_tsx as lang_fn
        elif name == "javascript":
            from tree_sitter_javascript import language as lang_fn
        elif name == "rust":
            from tree_sitter_rust import language as lang_fn
        elif name == "go":
            from tree_sitter_go import language as lang_fn
        else:
            raise ChunkerError(
                f"Unsupported language: {name}. Supported: {list(SUPPORTED_LANGUAGES)}",
                format=ChunkFormat.CODE,
            )
    except ImportError as e:
        raise ChunkerError(
            f"Grammar package for {name!r} is not installed: {e}",
            format=ChunkFormat.CODE,
        ) from e

    language = Language(lang_fn())
    _LANGUAGE_CACHE[name] = language
    return language


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
        "function_definition",       # python
        "method_definition",         # ts/js class methods
        "function_declaration",      # ts/js/go
        "generator_function_declaration",  # ts/js
        "function_item",             # rust
        "method_declaration",        # go
    }
    _node_types_class: ClassVar[set[str]] = {
        "class_definition",          # python
        "class_declaration",         # ts/js
        "struct_item",               # rust
        "enum_item",                 # rust
        "impl_item",                 # rust
        "trait_item",                # rust
        "type_declaration",          # go
        "interface_declaration",     # ts
    }
    _node_types_import: ClassVar[set[str]] = {
        "import_statement",          # python, ts/js
        "import_from_statement",     # python
        "import_clause",
        "import_from_clause",
        "import_declaration",        # go
        "use_declaration",           # rust
        "extern_crate_declaration",  # rust
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
        if language not in SUPPORTED_LANGUAGES:
            raise ChunkerError(
                f"Unsupported language: {language}. Supported: {list(SUPPORTED_LANGUAGES)}",
                format=self.format,
            )

        self.language = language
        self.config = config or CodeChunkConfig()
        self._parser = Parser(_load_language(language))
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
        chunks = self._fill_gaps(chunks, content)

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
        return self._detect_dependencies(merged, content)

    def _fill_gaps(self, chunks: list[Chunk], content: str) -> list[Chunk]:
        """Create body chunks for source not covered by any AST chunk.

        Module-level statements (assignments, top-level calls, arrow
        functions bound to consts, etc.) are not captured by the
        function/class/import visitors. Without this pass that content
        would be silently dropped before extraction ever sees it.

        Args:
            chunks: Chunks emitted by the AST visitor, any order.
            content: Original source text.

        Returns:
            Chunks plus gap-filling body chunks, sorted by position.
        """
        ordered = sorted(chunks, key=lambda c: c.start_pos)
        result: list[Chunk] = []
        cursor = 0

        # Byte offset of the start of each (0-based) line, for chunks
        # that carry precise end_line metadata.
        line_starts: list[int] = [0]
        for line in content.split("\n"):
            line_starts.append(line_starts[-1] + len(line) + 1)

        def chunk_end(chunk: Chunk) -> int:
            end_line = chunk.metadata.get("end_line")
            if isinstance(end_line, int) and 0 < end_line < len(line_starts):
                return line_starts[end_line]  # start of the line after end_line (1-based)
            return chunk.start_pos + len(chunk.content)

        def emit_gap(text: str, start: int) -> None:
            stripped = text.strip()
            if not stripped:
                return
            # Leading comments are already attached to the following chunk;
            # a gap that is nothing but comment lines would duplicate them.
            lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
            if all(ln.startswith(("#", "//", "/*", "*", '"""', "'''")) for ln in lines):
                return
            result.append(
                Chunk(
                    id=Chunk.compute_id(stripped),
                    content=stripped,
                    format=ChunkFormat.CODE,
                    chunk_type=ChunkType.CODE_BODY,
                    token_count=self._tokenizer.count(stripped),
                    start_pos=start,
                    dependencies=(),
                    metadata={},
                )
            )

        for chunk in ordered:
            end = chunk_end(chunk)
            if chunk.start_pos > cursor:
                emit_gap(content[cursor : chunk.start_pos], cursor)
            result.append(chunk)
            cursor = max(cursor, end)

        if cursor < len(content):
            emit_gap(content[cursor:], cursor)

        return sorted(result, key=lambda c: c.start_pos)

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

        # Functions and classes are semantic units the scorer must see
        # individually; merging two tiny functions into one chunk would
        # make one irrelevant function drag the other into (or out of)
        # the selection.
        never_merge = {ChunkType.CODE_FUNCTION, ChunkType.CODE_CLASS}

        result: list[Chunk] = []
        current: Chunk | None = None

        for chunk in chunks:
            if current is None:
                current = chunk
                continue

            if (
                current.token_count < self.config.min_chunk_tokens
                and current.chunk_type not in never_merge
                and chunk.chunk_type not in never_merge
            ):
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
            name = chunk.metadata.get("name")
            if name and name not in ("anonymous", "AnonymousClass"):
                name_map[name] = chunk.id

        if not name_map:
            return chunks

        # One pass with a single alternation regex instead of one scan
        # per known name — chunk lists can be large.
        name_pattern = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in name_map) + r")\b"
        )

        result: list[Chunk] = []
        for chunk in chunks:
            # Import blocks reference modules, not local definitions;
            # a name collision there would invert the dependency.
            if chunk.chunk_type == ChunkType.CODE_IMPORT_BLOCK:
                result.append(chunk)
                continue

            deps: list[str] = []
            seen: set[str] = set(chunk.dependencies)

            calls = chunk.metadata.get("calls")
            if isinstance(calls, list):
                # AST-derived call names: exact, no comment/string noise.
                for name in calls:
                    dep_id = name_map.get(name)
                    if dep_id and dep_id != chunk.id and dep_id not in seen:
                        seen.add(dep_id)
                        deps.append(dep_id)
            else:
                # No AST call info (body/docstring chunks): fall back to
                # name matching over the content.
                for match in name_pattern.finditer(chunk.content):
                    dep_id = name_map[match.group(1)]
                    if dep_id != chunk.id and dep_id not in seen:
                        seen.add(dep_id)
                        deps.append(dep_id)

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

    def _visit_function(
        self,
        node: Node,
        extra_deps: tuple[str, ...] = (),
        parent_class: str | None = None,
    ) -> None:
        """Extract a function definition as one chunk, or split if oversized.

        Args:
            node: Function definition AST node.
            extra_deps: Chunk IDs every emitted chunk depends on (the
                class-head chunk for methods of a split class).
            parent_class: Owning class name for methods of a split class.
        """
        start_byte = node.start_byte
        end_byte = node.end_byte
        content = self.content[start_byte:end_byte]

        func_name = self._get_function_name(node)
        calls = self._collect_calls(node)

        token_count_raw = self.tokenizer.count(content)
        if token_count_raw > self.config.max_chunk_tokens:
            self._emit_split_function(
                node, content, func_name, calls, extra_deps, parent_class
            )
            return

        leading_comments = self._get_leading_comments(node)
        if leading_comments:
            content = leading_comments + "\n" + content

        metadata: dict = {
            "name": func_name,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "calls": calls,
        }
        if parent_class:
            metadata["parent_class"] = parent_class

        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(content),
                content=content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_FUNCTION,
                token_count=self.tokenizer.count(content),
                start_pos=start_byte,
                dependencies=list(extra_deps),
                metadata=metadata,
            )
        )

    def _emit_split_function(
        self,
        node: Node,
        content: str,
        func_name: str,
        calls: list[str],
        extra_deps: tuple[str, ...],
        parent_class: str | None,
    ) -> None:
        """Split an oversized function into head + body continuation chunks.

        The head (signature, docstring, first lines) is a CODE_FUNCTION
        chunk; continuation parts are CODE_BODY chunks depending on the
        head, so a body part can never be selected without its signature.
        """
        max_tokens = self.config.max_chunk_tokens
        lines = content.split("\n")

        parts: list[tuple[str, int]] = []  # (part_content, byte_offset)
        buffer: list[str] = []
        buffer_tokens = 0
        offset = 0
        part_offset = 0

        for line in lines:
            line_tokens = self.tokenizer.count(line) + 1
            if buffer and buffer_tokens + line_tokens > max_tokens:
                parts.append(("\n".join(buffer), part_offset))
                buffer = []
                buffer_tokens = 0
                part_offset = offset
            buffer.append(line)
            buffer_tokens += line_tokens
            offset += len(line) + 1
        if buffer:
            parts.append(("\n".join(buffer), part_offset))

        head_content, _ = parts[0]
        head_id = Chunk.compute_id(head_content)

        head_metadata: dict = {
            "name": func_name,
            "start_line": node.start_point[0] + 1,
            # Cover the whole function so the gap filler doesn't
            # re-emit the body parts' source as duplicate body chunks.
            "end_line": node.end_point[0] + 1,
            "calls": calls,
            "split_parts": len(parts),
        }
        if parent_class:
            head_metadata["parent_class"] = parent_class

        self.chunks.append(
            Chunk(
                id=head_id,
                content=head_content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_FUNCTION,
                token_count=self.tokenizer.count(head_content),
                start_pos=node.start_byte,
                dependencies=list(extra_deps),
                metadata=head_metadata,
            )
        )

        for i, (part_content, part_byte_offset) in enumerate(parts[1:], start=1):
            self.chunks.append(
                Chunk(
                    id=Chunk.compute_id(part_content),
                    content=part_content,
                    format=ChunkFormat.CODE,
                    chunk_type=ChunkType.CODE_BODY,
                    token_count=self.tokenizer.count(part_content),
                    start_pos=node.start_byte + part_byte_offset,
                    dependencies=[head_id, *extra_deps],
                    metadata={
                        "parent_function": func_name,
                        "part": i,
                    },
                )
            )

    def _visit_class(self, node: Node) -> None:
        """Extract a class definition as one chunk, or split if oversized.

        Oversized classes split into a class-head chunk (declaration,
        docstring, attributes up to the first method) plus one chunk per
        method, each depending on the head.

        Args:
            node: Class definition AST node.
        """
        start_byte = node.start_byte
        end_byte = node.end_byte
        content = self.content[start_byte:end_byte]

        class_name = self._get_class_name(node)
        token_count = self.tokenizer.count(content)

        if token_count > self.config.max_chunk_tokens:
            methods = self._find_methods(node)
            if methods:
                self._emit_split_class(node, class_name, methods)
                return

        leading_comments = self._get_leading_comments(node)
        if leading_comments:
            content = leading_comments + "\n" + content

        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(content),
                content=content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_CLASS,
                token_count=self.tokenizer.count(content),
                start_pos=start_byte,
                dependencies=[],
                metadata={
                    "name": class_name,
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "calls": self._collect_calls(node),
                },
            )
        )

    def _emit_split_class(
        self, node: Node, class_name: str, methods: list[Node]
    ) -> None:
        """Split a class into head + per-method chunks + body segments."""
        head_content = self.content[node.start_byte : methods[0].start_byte].rstrip()
        head_id = Chunk.compute_id(head_content)

        self.chunks.append(
            Chunk(
                id=head_id,
                content=head_content,
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_CLASS,
                token_count=self.tokenizer.count(head_content),
                start_pos=node.start_byte,
                dependencies=[],
                metadata={
                    "name": class_name,
                    "start_line": node.start_point[0] + 1,
                    # Cover the whole class span for the gap filler.
                    "end_line": node.end_point[0] + 1,
                    "split_methods": len(methods),
                },
            )
        )

        cursor = methods[0].start_byte
        for method in methods:
            if method.start_byte > cursor:
                self._emit_class_segment(class_name, head_id, cursor, method.start_byte)
            self._visit_function(
                method, extra_deps=(head_id,), parent_class=class_name
            )
            cursor = method.end_byte

        if node.end_byte > cursor:
            self._emit_class_segment(class_name, head_id, cursor, node.end_byte)

    def _emit_class_segment(
        self, class_name: str, head_id: str, start: int, end: int
    ) -> None:
        """Emit non-method class content (attributes between methods)."""
        segment = self.content[start:end]
        if not segment.strip():
            return
        self.chunks.append(
            Chunk(
                id=Chunk.compute_id(segment),
                content=segment.strip("\n"),
                format=ChunkFormat.CODE,
                chunk_type=ChunkType.CODE_BODY,
                token_count=self.tokenizer.count(segment),
                start_pos=start,
                dependencies=[head_id],
                metadata={"parent_class": class_name},
            )
        )

    def _find_methods(self, node: Node) -> list[Node]:
        """Find a class's method nodes without descending into them."""
        found: list[Node] = []

        def walk(n: Node) -> None:
            for child in n.children:
                if child.type in CodeChunker._node_types_function:
                    found.append(child)
                else:
                    walk(child)

        walk(node)
        return sorted(found, key=lambda n: n.start_byte)

    _CALL_NODE_TYPES: ClassVar[set[str]] = {"call", "call_expression"}

    def _collect_calls(self, node: Node) -> list[str]:
        """Collect names of functions called within a node's subtree.

        Uses the AST's call expressions instead of regex matching, so a
        name in a comment or string no longer creates a dependency.
        For attribute/member calls (obj.method()), the rightmost
        identifier is used.
        """
        names: set[str] = set()

        def walk(n: Node) -> None:
            if n.type in self._CALL_NODE_TYPES:
                target = n.child_by_field_name("function")
                if target is not None:
                    text = self.content[target.start_byte : target.end_byte]
                    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", text)
                    if match:
                        names.add(match.group(1))
            for child in n.children:
                walk(child)

        walk(node)
        return sorted(names)

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
