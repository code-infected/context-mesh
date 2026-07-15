"""Test suite for log/html/csv/shell/mixed chunkers and multi-language code chunking."""

import json

from contextmesh.core.chunker.code_chunker import CodeChunker
from contextmesh.core.chunker.csv_chunker import CSVChunker
from contextmesh.core.chunker.html_chunker import HTMLChunker
from contextmesh.core.chunker.json_chunker import JSONChunker
from contextmesh.core.chunker.log_chunker import LogChunker
from contextmesh.core.chunker.mixed_chunker import MixedChunker
from contextmesh.core.chunker.shell_chunker import ShellChunker
from contextmesh.core.tokenizer import TokenCounter


class TestLogChunker:
    def test_groups_log_events(self) -> None:
        logs = "\n".join(
            f"2026-01-01 10:00:{i:02d} INFO service started step {i}" for i in range(30)
        )
        chunks = LogChunker().chunk(logs)
        assert chunks
        assert all(c.format.value == "log" for c in chunks)


class TestHTMLChunker:
    def test_chunks_semantic_sections(self) -> None:
        html = (
            "<html><body>"
            "<article><h1>Title</h1><p>" + "content " * 50 + "</p></article>"
            "<section><p>" + "more " * 50 + "</p></section>"
            "</body></html>"
        )
        chunks = HTMLChunker().chunk(html)
        assert chunks
        assert all(c.format.value == "html" for c in chunks)


class TestCSVChunker:
    def test_windows_rows(self) -> None:
        rows = ["col_a,col_b,col_c"] + [f"{i},{i * 2},{i * 3}" for i in range(120)]
        chunks = CSVChunker().chunk("\n".join(rows))
        assert chunks
        assert all(c.format.value == "csv" for c in chunks)


class TestShellChunker:
    def test_command_output_pairs(self) -> None:
        output = (
            "$ ls -la\n"
            "total 16\ndrwxr-xr-x file1\ndrwxr-xr-x file2\n"
            "$ cat README\n"
            "hello\nworld\nfoo\n"
        )
        chunks = ShellChunker().chunk(output)
        assert chunks
        assert all(c.format.value == "shell" for c in chunks)


class TestMixedChunker:
    def test_plain_text_falls_back(self) -> None:
        text = "\n\n".join("paragraph " * 30 for _ in range(5))
        chunks = MixedChunker().chunk(text)
        assert chunks


class TestCodeChunkerLanguages:
    def test_typescript_functions_and_classes(self) -> None:
        code = (
            "import { x } from './x';\n\n"
            "export function alpha(): number {\n  return 1;\n}\n\n"
            "export class Beta {\n  run(): void {}\n}\n"
        )
        chunks = CodeChunker("typescript").chunk(code)
        types = {c.chunk_type.value for c in chunks}
        assert "function" in types
        assert "class" in types
        assert "import_block" in types

    def test_javascript(self) -> None:
        code = "function greet(name) {\n  return 'hi ' + name;\n}\n"
        chunks = CodeChunker("javascript").chunk(code)
        assert any(c.chunk_type.value == "function" for c in chunks)

    def test_rust(self) -> None:
        code = (
            "use std::io;\n\n"
            "fn main() {\n    println!(\"hello\");\n}\n\n"
            "struct Point { x: i32, y: i32 }\n"
        )
        chunks = CodeChunker("rust").chunk(code)
        types = {c.chunk_type.value for c in chunks}
        assert "function" in types
        assert "class" in types  # struct maps to the class chunk type

    def test_go(self) -> None:
        code = (
            'package main\n\nimport "fmt"\n\n'
            'func main() {\n    fmt.Println("hello")\n}\n'
        )
        chunks = CodeChunker("go").chunk(code)
        assert any(c.chunk_type.value == "function" for c in chunks)

    def test_dependencies_detected(self) -> None:
        """A function calling another function depends on it."""
        code = (
            "def helper():\n    return 42\n\n\n"
            "def uses_helper():\n    value = helper()\n    return value + helper()\n"
        )
        chunks = CodeChunker("python").chunk(code)
        by_name = {c.metadata.get("name"): c for c in chunks if c.metadata.get("name")}
        assert "helper" in by_name and "uses_helper" in by_name
        assert by_name["helper"].id in by_name["uses_helper"].dependencies

    def test_module_level_code_not_lost(self) -> None:
        """Top-level statements outside functions land in body chunks."""
        code = (
            "CONSTANT_VALUE = 12345\n\n"
            "def foo():\n    return CONSTANT_VALUE\n\n"
            "another_constant = 'end of file value'\n"
        )
        chunks = CodeChunker("python").chunk(code)
        combined = "\n".join(c.content for c in chunks)
        assert "CONSTANT_VALUE = 12345" in combined
        assert "end of file value" in combined


class TestJSONChunkerProperties:
    def test_chunks_are_valid_json(self) -> None:
        data = {"a": {"b": list(range(100))}, "c": ["x" * 50] * 20}
        chunks = JSONChunker(max_chunk_tokens=50).chunk(json.dumps(data))
        assert chunks
        for c in chunks:
            json.loads(c.content)

    def test_no_content_blowup(self) -> None:
        """Regression: chunk token totals must stay near the input size.

        The old implementation re-extracted the enclosing document per
        leaf: a 10k-token input produced 5.6M tokens of chunks.
        """
        tokenizer = TokenCounter.get_default()
        data = {
            "users": [
                {"id": i, "name": f"user{i}", "notes": "text " * 20} for i in range(80)
            ]
        }
        raw = json.dumps(data)
        original_tokens = tokenizer.count(raw)

        chunks = JSONChunker().chunk(raw)
        total_chunk_tokens = sum(c.token_count for c in chunks)

        # Path prefixes add overhead, but nothing near duplication.
        assert total_chunk_tokens < original_tokens * 2

    def test_values_never_duplicated(self) -> None:
        """Each leaf value appears in exactly one chunk."""
        data = {"section_one": {"unique_marker_aaa": 1}, "section_two": {"unique_marker_bbb": 2}}
        chunks = JSONChunker(max_chunk_tokens=5, min_chunk_tokens=1).chunk(json.dumps(data))
        for marker in ("unique_marker_aaa", "unique_marker_bbb"):
            holders = [c for c in chunks if marker in c.content]
            assert len(holders) == 1, f"{marker} appears in {len(holders)} chunks"
