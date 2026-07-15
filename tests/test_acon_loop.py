"""Test suite for the ACON failure loop (spec: integration test).

Seeds the trace store with known failures, runs the guideline engine,
and verifies the guidelines table is updated with correct multipliers.
"""

from datetime import UTC, datetime, timedelta

from contextmesh.feedback.failure_detector import (
    FailureDetector,
    TaskOutcome,
    TaskOutcomeEvent,
)
from contextmesh.feedback.guideline_engine import (
    ACONConfig,
    ACONGuidelineEngine,
    RerunResult,
)
from contextmesh.feedback.trace_store import CompressionTrace, TraceStore


def make_trace(task_id: str, pruned_types: list[str]) -> CompressionTrace:
    return CompressionTrace(
        session_id="s1",
        task_id=task_id,
        tool_name="read_file",
        chunk_ids_pruned=[f"c{i}" for i in range(len(pruned_types))],
        chunk_types_pruned=pruned_types,
        original_token_count=1000,
        compressed_token_count=200,
        compression_ratio=0.2,
    )


class TestFailureDetector:
    """Tests for compression-failure pattern matching."""

    def test_import_error_implicates_compression(self) -> None:
        detector = FailureDetector()
        event = TaskOutcomeEvent(
            task_id="t1", session_id="s1", outcome=TaskOutcome.FAILED,
            failure_reason="ImportError: cannot import name 'verify_token'",
        )
        assert detector.process_outcome(event)

    def test_success_never_implicates(self) -> None:
        detector = FailureDetector()
        event = TaskOutcomeEvent(
            task_id="t1", session_id="s1", outcome=TaskOutcome.SUCCESS,
            failure_reason="ImportError",
        )
        assert not detector.process_outcome(event)

    def test_unrelated_failure_not_implicated(self) -> None:
        detector = FailureDetector()
        event = TaskOutcomeEvent(
            task_id="t1", session_id="s1", outcome=TaskOutcome.FAILED,
            failure_reason="network timeout connecting to db",
        )
        assert not detector.process_outcome(event)


class TestACONGuidelineEngine:
    """Tests for the ACON guideline update loop."""

    def test_update_gated_on_min_failures(self) -> None:
        """No multiplier update until min_failures_before_update tasks fail."""
        engine = ACONGuidelineEngine(config=ACONConfig(min_failures_before_update=3))

        for i in range(2):
            engine.analyze_task_failure(f"t{i}", [make_trace(f"t{i}", ["import_block"])])
        assert engine.guideline_store.get_multiplier("read_file", "import_block") == 1.0

        analysis = engine.analyze_task_failure("t2", [make_trace("t2", ["import_block"])])
        assert engine.guideline_store.get_multiplier("read_file", "import_block") == 1.2
        assert len(analysis.updates) == 1
        assert analysis.updates[0].old_multiplier == 1.0
        assert analysis.updates[0].new_multiplier == 1.2

    def test_one_task_counts_once_per_chunk_type(self) -> None:
        """Many pruned chunks of one type in one task = one failure count."""
        engine = ACONGuidelineEngine(config=ACONConfig(min_failures_before_update=3))

        engine.analyze_task_failure("t0", [make_trace("t0", ["function"] * 50)])
        assert engine.guideline_store.get_multiplier("read_file", "function") == 1.0

    def test_multiplier_capped(self) -> None:
        """Multiplier never exceeds the configured cap."""
        engine = ACONGuidelineEngine(
            config=ACONConfig(min_failures_before_update=1, max_multiplier=3.0)
        )
        for i in range(30):
            engine.analyze_task_failure(f"t{i}", [make_trace(f"t{i}", ["function"])])
        assert engine.guideline_store.get_multiplier("read_file", "function") <= 3.0

    def test_rerun_failure_exonerates_compression(self) -> None:
        """If the full-context re-run also fails, no guideline changes."""
        engine = ACONGuidelineEngine(config=ACONConfig(min_failures_before_update=1))

        analysis = engine.analyze_task_failure(
            "t0", [make_trace("t0", ["function"])],
            rerun_agent=lambda tid: RerunResult(success=False),
        )
        assert not analysis.compression_implicated
        assert engine.guideline_store.get_multiplier("read_file", "function") == 1.0

    def test_rerun_diff_excludes_unrelated_chunks(self) -> None:
        """Pruned chunks absent from the successful re-run are not root causes."""
        engine = ACONGuidelineEngine(config=ACONConfig(min_failures_before_update=1))

        analysis = engine.analyze_task_failure(
            "t0", [make_trace("t0", ["function"])],
            chunk_contents={"c0": "def totally_unrelated(): pass"},
            rerun_agent=lambda tid: RerunResult(
                success=True, accessed_content="import auth; auth.verify()"
            ),
        )
        assert analysis.compression_implicated
        assert analysis.root_cause_chunk_ids == []
        assert engine.guideline_store.get_multiplier("read_file", "function") == 1.0

    def test_decay_after_quiet_period(self) -> None:
        """Multipliers decay toward 1.0 after decay_days without failures."""
        engine = ACONGuidelineEngine(
            config=ACONConfig(min_failures_before_update=1, decay_days=30)
        )
        engine.analyze_task_failure("t0", [make_trace("t0", ["function"])])
        assert engine.guideline_store.get_multiplier("read_file", "function") == 1.2

        engine._last_failure_at[("read_file", "function")] = (
            datetime.now(UTC) - timedelta(days=31)
        )
        decayed = engine.apply_decay()

        assert decayed == 1
        assert engine.guideline_store.get_multiplier("read_file", "function") < 1.2

    def test_history_records_updates(self) -> None:
        """Every applied update lands in the audit history."""
        engine = ACONGuidelineEngine(config=ACONConfig(min_failures_before_update=1))
        engine.analyze_task_failure("t0", [make_trace("t0", ["function"])])

        history = engine.get_history()
        assert len(history) == 1
        assert history[0]["tool_name"] == "read_file"
        assert history[0]["chunk_type"] == "function"
        assert history[0]["task_id"] == "t0"


class TestTraceStoreIntegration:
    """End-to-end: seed store with a failure, run the engine, verify."""

    def test_seeded_failure_updates_guidelines(self) -> None:
        store = TraceStore()
        engine = ACONGuidelineEngine(config=ACONConfig(min_failures_before_update=1))

        trace = make_trace("task_fail", ["import_block", "function"])
        store.record(trace)

        traces = store.get_traces_for_task("task_fail")
        assert len(traces) == 1

        analysis = engine.analyze_task_failure("task_fail", traces)

        assert analysis.compression_implicated
        assert engine.guideline_store.get_multiplier("read_file", "import_block") == 1.2
        assert engine.guideline_store.get_multiplier("read_file", "function") == 1.2
        evidence = engine.guideline_store.get_evidence("read_file", "import_block")
        assert "task_fail" in evidence
