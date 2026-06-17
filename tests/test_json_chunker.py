"""Test suite for JSON chunker."""

import pytest
from contextmesh.core.chunker.json_chunker import JSONChunker


class TestJSONChunker:
    """Tests for JSONChunker."""

    def test_chunks_nested_objects(self) -> None:
        """Test nested JSON objects are chunked by depth."""
        data = '{"users": {"id": 1, "name": "Alice"}, "settings": {"theme": "dark"}}'
        chunker = JSONChunker(max_depth=2)
        chunks = chunker.chunk(data)

        assert len(chunks) > 0
        assert all(c.format.value == "json" for c in chunks)

    def test_handles_flat_json(self) -> None:
        """Test flat JSON is handled."""
        data = '{"id": 1, "name": "Alice", "age": 30}'
        chunker = JSONChunker()
        chunks = chunker.chunk(data)

        assert len(chunks) >= 1

    def test_handles_json_arrays(self) -> None:
        """Test JSON arrays are chunked."""
        data = '[{"id": 1}, {"id": 2}, {"id": 3}]'
        chunker = JSONChunker(max_depth=1)
        chunks = chunker.chunk(data)

        assert len(chunks) >= 1

    def test_handles_invalid_json(self) -> None:
        """Test invalid JSON raises ChunkerError."""
        from contextmesh.core.chunker.base import ChunkerError

        chunker = JSONChunker()
        with pytest.raises(ChunkerError):
            chunker.chunk("not valid json")

    def test_handles_empty_input(self) -> None:
        """Test empty input returns empty list."""
        chunker = JSONChunker()
        chunks = chunker.chunk("")
        assert chunks == []
