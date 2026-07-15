"""End-to-end tests for the compression pipeline (fallback scorer)."""

import json
from pathlib import Path

from contextmesh.config import create_pipeline, load_config
from contextmesh.core.chunker.base import CompressionInput
from contextmesh.feedback.trace_store import TraceStore

FIXTURE = Path(__file__).parent / "fixtures" / "large_python_file.py"


def patient_pipeline():
    """Pipeline with a generous deadline: these are functional tests,
    and cold-cache encoding under a CPU-saturated test run can exceed
    the production default."""
    pipeline = create_pipeline(load_config())
    pipeline.config.hard_timeout_ms = 120000
    return pipeline


def make_input(raw_output: str, **overrides) -> CompressionInput:
    defaults = {
        "session_id": "s1",
        "task_id": "t1",
        "tool_name": "read_file",
        "tool_args": {"path": "/src/auth.py"},
        "raw_output": raw_output,
        "task_description": "find all authentication-related functions",
        "budget_tokens": 1200,
    }
    defaults.update(overrides)
    return CompressionInput(**defaults)


class TestPipelineEndToEnd:
    def test_code_compression_under_budget(self) -> None:
        pipeline = patient_pipeline()
        out = pipeline.compress(make_input(FIXTURE.read_text(encoding="utf-8")))

        assert out.compression_ratio < 0.5
        assert out.compressed_tokens <= 1200 * 1.2  # budget + dependency slack
        assert out.chunks_selected < out.chunks_total
        assert "def " in out.compressed_output

    def test_small_output_skipped(self) -> None:
        pipeline = patient_pipeline()
        out = pipeline.compress(make_input("def tiny(): pass"))

        assert out.compression_ratio == 1.0
        assert out.compressed_output == "def tiny(): pass"

    def test_json_compression_produces_valid_json(self) -> None:
        data = {
            "users": [
                {"id": i, "name": f"user{i}", "bio": "lorem ipsum " * 30}
                for i in range(60)
            ]
        }
        pipeline = patient_pipeline()
        out = pipeline.compress(
            make_input(
                json.dumps(data),
                tool_name="query_database",
                tool_args={"q": "select"},
                task_description="find user 42 email address",
                budget_tokens=1500,
            )
        )

        assert out.compression_ratio < 1.0
        json.loads(out.compressed_output)

    def test_traces_recorded_when_store_attached(self) -> None:
        store = TraceStore()
        pipeline = patient_pipeline()
        pipeline.trace_store = store

        out = pipeline.compress(make_input(FIXTURE.read_text(encoding="utf-8")))

        traces = store.get_all_traces()
        assert len(traces) == 1
        assert traces[0].id == out.trace_id
        assert traces[0].tool_name == "read_file"
        assert traces[0].chunk_ids_pruned  # something was pruned
        assert (
            len(traces[0].chunk_ids_selected) + len(traces[0].chunk_ids_pruned)
            == out.chunks_total
        )

    def test_language_inferred_from_path(self) -> None:
        code = (
            "import { readFile } from 'fs/promises';\n\n"
            + "\n\n".join(
                f"export function handler{i}(input: string): string {{\n"
                f"  const value = input + '{i}';\n"
                f"  const trimmed = value.trim().toLowerCase();\n"
                f"  const encoded = encodeURIComponent(trimmed);\n"
                f"  return encoded + '-suffix-{i}';\n}}"
                for i in range(60)
            )
        )
        pipeline = patient_pipeline()
        out = pipeline.compress(
            make_input(code, tool_args={"path": "/src/handlers.ts"}, budget_tokens=800)
        )
        assert out.compression_ratio < 1.0


class TestScorerFallback:
    def test_fallback_encoder_is_deterministic(self) -> None:
        from contextmesh.core.scorer.embed_scorer import LexicalFallbackEncoder

        enc = LexicalFallbackEncoder()
        a = enc.encode(["def authenticate_user(): pass"])
        b = enc.encode(["def authenticate_user(): pass"])
        assert (a == b).all()

    def test_cache_hit_rate_tracked(self) -> None:
        import numpy as np

        from contextmesh.core.scorer.cache import EmbeddingCache

        cache = EmbeddingCache(max_size=10)
        cache.put("k", np.zeros(4))
        assert cache.get("k") is not None
        assert cache.get("missing") is None
        assert cache.hit_rate() == 0.5
