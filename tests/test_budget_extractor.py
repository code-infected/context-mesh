"""Test suite for budget extractor."""

from contextmesh.core.chunker.base import Chunk, ChunkFormat, ChunkType, ScoredChunk
from contextmesh.core.chunker.dependency_graph import DependencyGraph
from contextmesh.core.extractor.budget_extractor import BudgetExtractor


class TestBudgetExtractor:
    """Tests for BudgetExtractor."""

    def test_extracts_under_budget(self) -> None:
        """Test chunks are selected under budget."""
        chunks = [
            Chunk(
                id="1", content="a" * 100, format=ChunkFormat.TEXT,
                chunk_type=ChunkType.TEXT_PARAGRAPH, token_count=25,
                start_pos=0, dependencies=()
            ),
            Chunk(
                id="2", content="b" * 200, format=ChunkFormat.TEXT,
                chunk_type=ChunkType.TEXT_PARAGRAPH, token_count=50,
                start_pos=100, dependencies=()
            ),
            Chunk(
                id="3", content="c" * 300, format=ChunkFormat.TEXT,
                chunk_type=ChunkType.TEXT_PARAGRAPH, token_count=75,
                start_pos=200, dependencies=()
            ),
        ]

        scored = [ScoredChunk(chunk=c, score=1.0 - i * 0.1) for i, c in enumerate(chunks)]

        extractor = BudgetExtractor()
        graph = DependencyGraph()
        for c in chunks:
            graph.add_chunk(c)

        selected = extractor.extract(scored, budget=100, dependency_graph=graph)

        total_tokens = sum(c.token_count for c in selected)
        assert total_tokens <= 100

    def test_respects_dependencies(self) -> None:
        """Test dependencies are included when parent selected."""
        chunks = [
            Chunk(
                id="parent", content="parent content",
                format=ChunkFormat.CODE, chunk_type=ChunkType.CODE_FUNCTION,
                token_count=50, start_pos=0, dependencies=()
            ),
            Chunk(
                id="child", content="child content",
                format=ChunkFormat.CODE, chunk_type=ChunkType.CODE_FUNCTION,
                token_count=30, start_pos=50, dependencies=("parent",)
            ),
        ]

        scored = [ScoredChunk(chunk=chunks[1], score=1.0)]

        extractor = BudgetExtractor()
        graph = DependencyGraph()
        for c in chunks:
            graph.add_chunk(c)

        selected = extractor.extract(scored, budget=80, dependency_graph=graph)

        selected_ids = {c.id for c in selected}
        assert "parent" in selected_ids
        assert "child" in selected_ids

    def test_handles_empty_input(self) -> None:
        """Test empty chunks returns empty list."""
        extractor = BudgetExtractor()
        graph = DependencyGraph()
        selected = extractor.extract([], budget=1000, dependency_graph=graph)
        assert selected == []
