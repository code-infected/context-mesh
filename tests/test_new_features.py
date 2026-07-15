"""Tests for the hardening/feature round: markdown chunking, function
splitting, AST deps, result cache, session dedup, chunk cap, prefilter,
guideline persistence wiring."""

import json

from contextmesh.core.chunker.base import ChunkFormat, ChunkType, CompressionInput
from contextmesh.core.chunker.code_chunker import CodeChunkConfig, CodeChunker
from contextmesh.core.chunker.markdown_chunker import MarkdownChunker
from contextmesh.core.pipeline import CompressionPipeline, PipelineConfig
from contextmesh.feedback.trace_store import TraceStore


def make_input(raw: str, task_id: str = "t1", session_id: str = "s1", **kw) -> CompressionInput:
    defaults = {
        "session_id": session_id,
        "task_id": task_id,
        "tool_name": "read_file",
        "tool_args": {"path": "/src/x.py"},
        "raw_output": raw,
        "task_description": "find the parser helpers",
        "budget_tokens": 1200,
    }
    defaults.update(kw)
    return CompressionInput(**defaults)


class TestMarkdownChunker:
    def test_sections_by_heading(self) -> None:
        md = (
            "intro before headings\n\n"
            "# One\n\ncontent one " * 3 + "\n\n## Two\n\ncontent two\n"
        )
        chunks = MarkdownChunker().chunk(md)
        assert chunks
        assert all(c.format == ChunkFormat.MARKDOWN for c in chunks)
        assert any(c.chunk_type == ChunkType.MARKDOWN_PREAMBLE for c in chunks)

    def test_heading_inside_fence_is_not_boundary(self) -> None:
        md = "# Real\n\ntext\n\n```\n# not a heading\ncode()\n```\nmore text\n"
        chunks = MarkdownChunker(min_chunk_tokens=1).chunk(md)
        combined = "\n".join(c.content for c in chunks)
        assert "# not a heading" in combined
        headings = [c.metadata.get("heading") for c in chunks]
        assert "not a heading" not in headings

    def test_empty_input(self) -> None:
        assert MarkdownChunker().chunk("") == []

    def test_pipeline_detects_markdown(self) -> None:
        pipeline = CompressionPipeline()
        assert pipeline._detect_format("# Title\n\nSome prose here.") == ChunkFormat.MARKDOWN


class TestFunctionSplitting:
    def test_oversized_function_splits_with_head_dependency(self) -> None:
        body_lines = "\n".join(f"    value_{i} = compute_{i}()" for i in range(120))
        code = f"def enormous():\n{body_lines}\n    return 1\n"
        chunker = CodeChunker("python", CodeChunkConfig(max_chunk_tokens=200))
        chunks = chunker.chunk(code)

        heads = [c for c in chunks if c.chunk_type == ChunkType.CODE_FUNCTION]
        bodies = [c for c in chunks if c.chunk_type == ChunkType.CODE_BODY]
        assert len(heads) == 1
        assert bodies, "oversized function should produce body parts"
        head = heads[0]
        assert all(head.id in b.dependencies for b in bodies)
        assert all(c.token_count <= 260 for c in chunks)  # max + line slack

    def test_oversized_class_splits_into_methods(self) -> None:
        methods = "\n\n".join(
            f"    def method_{i}(self):\n        return {i} + self.base"
            for i in range(30)
        )
        code = f"class Big:\n    base = 1\n\n{methods}\n"
        chunker = CodeChunker("python", CodeChunkConfig(max_chunk_tokens=100))
        chunks = chunker.chunk(code)

        class_heads = [c for c in chunks if c.chunk_type == ChunkType.CODE_CLASS]
        method_chunks = [
            c for c in chunks
            if c.chunk_type == ChunkType.CODE_FUNCTION
            and c.metadata.get("parent_class") == "Big"
        ]
        assert len(class_heads) == 1
        assert len(method_chunks) == 30
        head_id = class_heads[0].id
        assert all(head_id in m.dependencies for m in method_chunks)

    def test_ast_calls_beat_comment_mentions(self) -> None:
        """A name in a comment must not create a dependency."""
        code = (
            "def helper():\n    return 42\n\n\n"
            "def commented():\n    # helper is unrelated here\n    return 0\n\n\n"
            "def caller():\n    return helper()\n"
        )
        chunks = CodeChunker("python").chunk(code)
        by_name = {c.metadata.get("name"): c for c in chunks if c.metadata.get("name")}
        helper_id = by_name["helper"].id
        assert helper_id in by_name["caller"].dependencies
        assert helper_id not in by_name["commented"].dependencies


class TestFormatDetection:
    def test_csv_detected_and_routed(self) -> None:
        pipeline = CompressionPipeline()
        csv_content = "\n".join(
            ["order_id,customer,status,total"]
            + [f"{i},cust_{i},shipped,{i * 3}.50" for i in range(40)]
        )
        assert pipeline._detect_format(csv_content) == ChunkFormat.CSV
        chunks = pipeline._chunk(csv_content, None)
        assert chunks
        assert all(c.format == ChunkFormat.CSV for c in chunks)

    def test_prose_with_commas_is_not_csv(self) -> None:
        pipeline = CompressionPipeline()
        prose = (
            "First, we consider the problem.\n"
            "Then, after review, we act, decisively.\n"
            "Finally we conclude.\n"
        )
        assert pipeline._detect_format(prose) != ChunkFormat.CSV


class TestPipelineBounds:
    def test_chunk_cap_coarsens(self) -> None:
        pipeline = CompressionPipeline(PipelineConfig(max_chunks=10))
        chunks = pipeline._chunk(
            json.dumps({f"key_{i}": ("v " * 40) for i in range(50)}), None
        )
        capped = pipeline._cap_chunks(chunks)
        assert len(capped) <= 10

    def test_prefilter_truncates_middle(self) -> None:
        pipeline = CompressionPipeline(PipelineConfig(prefilter_half_tokens=100))
        content = "start marker " + ("word " * 3000) + " end marker"
        original_tokens = pipeline.tokenizer.count(content)
        filtered = pipeline._prefilter(content, original_tokens)
        assert "start marker" in filtered
        assert "end marker" in filtered
        assert pipeline.tokenizer.count(filtered) < original_tokens

    def test_result_cache_hits(self) -> None:
        store = TraceStore()
        # Functional test: generous deadline so CPU-saturated CI/dev
        # runs don't fail open mid-test.
        pipeline = CompressionPipeline(
            PipelineConfig(hard_timeout_ms=120000), trace_store=store
        )
        code = "\n\n".join(
            f"def parser_helper_{i}(text):\n    cleaned = text.strip()\n"
            f"    return cleaned + '{i}'" for i in range(200)
        )
        out1 = pipeline.compress(make_input(code, task_id="t1", budget_tokens=800))
        out2 = pipeline.compress(make_input(code, task_id="t2", budget_tokens=800))

        assert out1.compression_ratio < 1.0
        assert out2.compressed_output == out1.compressed_output
        # Cache hit records its own trace under the new task id.
        assert out2.trace_id != out1.trace_id
        task_ids = {t.task_id for t in store.get_all_traces()}
        assert {"t1", "t2"} <= task_ids

    def test_session_dedup_suppresses_repeat_content(self) -> None:
        pipeline = CompressionPipeline(
            PipelineConfig(
                session_dedup_enabled=True,
                result_cache_size=0,
                hard_timeout_ms=120000,
            )
        )
        code = "\n\n".join(
            f"def dedup_helper_{i}(text):\n    cleaned = text.strip()\n"
            f"    return cleaned + '{i}'" for i in range(200)
        )
        out1 = pipeline.compress(make_input(code, task_id="t1", budget_tokens=800))
        assert out1.compression_ratio < 1.0

        out2 = pipeline.compress(make_input(code, task_id="t2", budget_tokens=800))
        # Chunks delivered in call 1 must never be re-sent: the second
        # call returns only fresh (previously pruned) chunks.
        import re

        names1 = set(re.findall(r"def (dedup_helper_\d+)", out1.compressed_output))
        names2 = set(re.findall(r"def (dedup_helper_\d+)", out2.compressed_output))
        assert names1 and names2
        assert not names1 & names2, f"re-delivered chunks: {names1 & names2}"


class TestOversizedChunkFailOpen:
    def test_giant_single_line_never_returns_empty(self) -> None:
        """Regression: a single chunk bigger than the budget used to
        produce an empty selection and return "" as the compression."""
        pipeline = CompressionPipeline(PipelineConfig(hard_timeout_ms=120000))
        # One enormous line: unchunkable semantically, exceeds budget.
        blob = "def check_fn_XYZ(): return 1\\n" * 400  # literal \n, one line
        out = pipeline.compress(make_input(blob, budget_tokens=800))

        assert out.compressed_output != ""
        assert out.compressed_tokens > 0
        # Either it compressed via oversize-splitting or failed open —
        # both acceptable; silent emptiness is not.
        if out.compression_ratio < 1.0:
            assert "check_fn_XYZ" in out.compressed_output


class TestGuidelinePersistenceWiring:
    def test_engine_loads_persisted_records(self) -> None:
        from contextmesh.feedback.guideline_engine import ACONGuidelineEngine

        class FakePersistence:
            available = True

            def load_guidelines(self):
                return [{
                    "tool_name": "read_file", "chunk_type": "import_block",
                    "score_multiplier": 1.44, "update_count": 2,
                    "evidence_task_ids": ["t9"], "last_updated": "2026-07-14T00:00:00+00:00",
                }]

            def upsert_guideline(self, record):
                self.saved = record

            def record_history(self, update):
                self.history = update

        persistence = FakePersistence()
        engine = ACONGuidelineEngine(persistence=persistence)
        assert engine.guideline_store.get_multiplier("read_file", "import_block") == 1.44
