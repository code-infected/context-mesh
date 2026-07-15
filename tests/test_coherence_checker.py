"""Test suite for coherence checker."""

import json

from contextmesh.core.chunker.base import Chunk, ChunkFormat, ChunkType
from contextmesh.core.chunker.dependency_graph import DependencyGraph
from contextmesh.core.validator.coherence_checker import CoherenceChecker


def make_chunk(
    chunk_id: str,
    content: str,
    fmt: ChunkFormat,
    chunk_type: ChunkType,
    start_pos: int = 0,
    dependencies: tuple = (),
    metadata: dict | None = None,
) -> Chunk:
    return Chunk(
        id=chunk_id,
        content=content,
        format=fmt,
        chunk_type=chunk_type,
        token_count=len(content) // 4,
        start_pos=start_pos,
        dependencies=dependencies,
        metadata=metadata or {},
    )


class TestCoherenceChecker:
    """Tests for CoherenceChecker."""

    def test_valid_selection_passes(self) -> None:
        """A dependency-complete selection passes unchanged."""
        chunks = [
            make_chunk("a", "def foo(): pass", ChunkFormat.CODE, ChunkType.CODE_FUNCTION),
        ]
        graph = DependencyGraph()
        for c in chunks:
            graph.add_chunk(c)

        result = CoherenceChecker().validate(chunks, budget=1000, dependency_graph=graph)

        assert result.valid
        assert result.violations_fixed == 0
        assert [c.id for c in result.chunks] == ["a"]

    def test_code_force_includes_missing_dependencies(self) -> None:
        """A selected chunk pulls in pruned chunks it depends on."""
        helper = make_chunk(
            "helper", "def helper(): pass",
            ChunkFormat.CODE, ChunkType.CODE_FUNCTION, start_pos=100,
        )
        caller = make_chunk(
            "caller", "def main(): helper()",
            ChunkFormat.CODE, ChunkType.CODE_FUNCTION,
            start_pos=0, dependencies=("helper",),
        )
        graph = DependencyGraph()
        graph.add_chunk(caller)
        graph.add_chunk(helper)

        result = CoherenceChecker().validate(
            [caller], budget=1000, dependency_graph=graph
        )

        assert "helper" in {c.id for c in result.chunks}

    def test_code_never_pulls_in_dependents(self) -> None:
        """Selecting a helper must NOT cascade its callers in."""
        helper = make_chunk(
            "helper", "def helper(): pass",
            ChunkFormat.CODE, ChunkType.CODE_FUNCTION, start_pos=100,
        )
        caller = make_chunk(
            "caller", "def main(): helper()",
            ChunkFormat.CODE, ChunkType.CODE_FUNCTION,
            start_pos=0, dependencies=("helper",),
        )
        graph = DependencyGraph()
        graph.add_chunk(caller)
        graph.add_chunk(helper)

        result = CoherenceChecker().validate(
            [helper], budget=1000, dependency_graph=graph
        )

        assert "caller" not in {c.id for c in result.chunks}

    def test_broken_json_chunk_is_dropped(self) -> None:
        """A JSON chunk that doesn't parse is removed from the selection."""
        good = make_chunk(
            "good", json.dumps({"root.a": 1}), ChunkFormat.JSON, ChunkType.JSON_LEAF,
        )
        broken = make_chunk(
            "broken", '{"root.b": ', ChunkFormat.JSON, ChunkType.JSON_LEAF, start_pos=1,
        )
        graph = DependencyGraph()
        graph.add_chunk(good)
        graph.add_chunk(broken)

        result = CoherenceChecker().validate(
            [good, broken], budget=1000, dependency_graph=graph
        )

        ids = {c.id for c in result.chunks}
        assert "good" in ids
        assert "broken" not in ids

    def test_empty_selection_is_valid(self) -> None:
        """Empty input is trivially coherent."""
        result = CoherenceChecker().validate([], budget=1000, dependency_graph=DependencyGraph())
        assert result.valid
        assert result.chunks == []
