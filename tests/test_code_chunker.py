"""Test suite for code chunker."""

import pytest

from contextmesh.core.chunker.code_chunker import CodeChunker


class TestCodeChunker:
    """Tests for CodeChunker."""

    def test_chunks_function_definitions(self) -> None:
        """Test that functions are chunked correctly."""
        code = '''
def foo():
    pass

def bar():
    pass
'''
        chunker = CodeChunker("python")
        chunks = chunker.chunk(code)

        assert len(chunks) >= 2
        func_chunks = [c for c in chunks if c.chunk_type.value == "function"]
        assert len(func_chunks) >= 2

    def test_chunks_import_blocks(self) -> None:
        """Test that imports are grouped."""
        code = '''
import os
import sys
from typing import List

def main():
    pass
'''
        chunker = CodeChunker("python")
        chunks = chunker.chunk(code)

        import_chunks = [c for c in chunks if c.chunk_type.value == "import_block"]
        assert len(import_chunks) >= 1

    def test_handles_empty_input(self) -> None:
        """Test empty input returns empty list."""
        chunker = CodeChunker("python")
        chunks = chunker.chunk("")
        assert chunks == []

    def test_handles_unsupported_language(self) -> None:
        """Test unsupported language raises error at construction."""
        from contextmesh.core.chunker.base import ChunkerError

        with pytest.raises(ChunkerError):
            CodeChunker("unsupported_language")
