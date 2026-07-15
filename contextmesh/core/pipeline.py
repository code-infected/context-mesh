"""Compression pipeline orchestrator.

Orchestrates the full compression flow:
    Type detection -> Chunker -> Dependency graph ->
    Scorer -> Extractor -> Validator -> Output

Architecture:
    Input -> chunker -> scorer -> extractor -> validator -> Output
              |          |          |           |
              v          v          v           v
           chunks    scored    selected      validated
                      chunks    chunks         chunks

Robustness properties:
- Thread-safe: no per-call state on the pipeline instance; one pipeline
  may serve concurrent compress() calls.
- Hard deadline: compression races a configurable timeout and fails
  open (returns the raw output) when exceeded.
- Bounded work: giant outputs are pre-filtered to head+tail and chunk
  counts are capped by coarsening before scoring.
- Result cache: identical (output, tool, task context, budget) calls
  return a cached result without recomputing.
- Optional inter-call dedup: chunks already delivered to a session can
  be dropped from later outputs (Squeez-style, off by default).
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from datetime import UTC
from typing import TYPE_CHECKING

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkFormat,
    CompressionInput,
    CompressionOutput,
    ScoredChunk,
    TaskContext,
)
from contextmesh.core.chunker.code_chunker import CodeChunker
from contextmesh.core.chunker.csv_chunker import CSVChunker
from contextmesh.core.chunker.dependency_graph import DependencyGraph
from contextmesh.core.chunker.html_chunker import HTMLChunker
from contextmesh.core.chunker.json_chunker import JSONChunker
from contextmesh.core.chunker.log_chunker import LogChunker
from contextmesh.core.chunker.markdown_chunker import MarkdownChunker
from contextmesh.core.chunker.mixed_chunker import MixedChunker
from contextmesh.core.chunker.shell_chunker import ShellChunker
from contextmesh.core.extractor.budget_extractor import BudgetExtractor, ExtractorConfig
from contextmesh.core.scorer.embed_scorer import EmbedScorer
from contextmesh.core.scorer.guideline_adjuster import GuidelineAdjuster
from contextmesh.core.tokenizer import TokenCounter
from contextmesh.core.validator.coherence_checker import CoherenceChecker

if TYPE_CHECKING:
    from contextmesh.core.chunker.base import ChunkerBase

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the compression pipeline.

    Attributes:
        default_budget_tokens: Default token budget per call.
        max_overhead_ms: Soft latency target; exceeding it logs a warning.
        hard_timeout_ms: Hard deadline; exceeding it aborts compression
            and returns the raw output (fail-open).
        min_compression_ratio: Minimum savings worth keeping; output
            above (1 - this) of the original size returns the original.
        enable_coherence_validation: Whether to run the validator.
        low_signal_std_threshold: Score-uniformity threshold for the
            vague-task fail-open path.
        max_chunks: Chunk-count cap; larger sets are coarsened by
            merging adjacent chunks before scoring.
        prefilter_half_tokens: For enormous outputs, keep this many
            tokens from the head and tail before chunking (0 disables).
        result_cache_size: LRU entries for the compression result cache
            (0 disables).
        session_dedup_enabled: Drop chunks already delivered to the
            same session in earlier calls (off by default).
        trace_preview_chars: Per-chunk content preview stored in traces
            for the dashboard diff view (0 disables previews).
        trace_preview_max_chunks: Skip previews when a call produces
            more chunks than this (keeps traces bounded).
    """

    default_budget_tokens: int = 8000
    max_overhead_ms: int = 80
    # Deadline must leave room for cold-cache embedding on a slow CPU;
    # the 80ms soft target assumes warm caches / decent hardware.
    hard_timeout_ms: int = 10000
    min_compression_ratio: float = 0.3
    enable_coherence_validation: bool = True
    low_signal_std_threshold: float = 0.05
    max_chunks: int = 2000
    prefilter_half_tokens: int = 30000
    result_cache_size: int = 256
    session_dedup_enabled: bool = False
    trace_preview_chars: int = 300
    trace_preview_max_chunks: int = 500


_DEDUP_NOTICE = (
    "[ContextMesh] This tool output was suppressed: all of its relevant "
    "content was already delivered earlier in this session."
)


class CompressionPipeline:
    """Main compression pipeline orchestrator.

    Coordinates all compression components into a single compress()
    call that returns a CompressionOutput. Safe to share across
    threads.

    Example:
        >>> pipeline = CompressionPipeline()
        >>> inp = CompressionInput(
        ...     session_id="s1",
        ...     task_id="t1",
        ...     tool_name="read_file",
        ...     tool_args={"path": "/src/main.py"},
        ...     raw_output="def foo(): pass",
        ...     task_description="find all functions",
        ...     budget_tokens=6000,
        ... )
        >>> out = pipeline.compress(inp)
    """

    def __init__(
        self,
        config: PipelineConfig | None = None,
        chunker: ChunkerBase | None = None,
        scorer: EmbedScorer | None = None,
        guideline_adjuster: GuidelineAdjuster | None = None,
        extractor: BudgetExtractor | None = None,
        validator: CoherenceChecker | None = None,
        trace_store: object | None = None,
    ) -> None:
        """Initialize compression pipeline.

        Args:
            config: Optional pipeline configuration.
            chunker: Optional chunker override (used for TEXT format).
            scorer: Optional scorer override.
            guideline_adjuster: Optional guideline adjuster override.
            extractor: Optional extractor override.
            validator: Optional validator override.
            trace_store: Optional feedback.trace_store.TraceStore; when
                set, every compression records a trace for the ACON loop
                and the dashboard.
        """
        self.config = config or PipelineConfig()
        self.chunker = chunker or MixedChunker()
        self.scorer = scorer or EmbedScorer()
        self.guideline_adjuster = guideline_adjuster or GuidelineAdjuster()
        self.extractor = extractor or BudgetExtractor(ExtractorConfig())
        self.validator = validator or CoherenceChecker()
        self.tokenizer = TokenCounter.get_default()
        self.trace_store = trace_store

        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="cm-compress"
        )
        self._warmed_up = False
        self._warmup_lock = threading.Lock()

        self._result_cache: OrderedDict[str, CompressionOutput] = OrderedDict()
        self._cache_lock = threading.Lock()
        # Trace templates for cache hits: a cached result still represents
        # a real tool call, so each hit records a fresh trace (otherwise
        # the ACON loop finds no traces for tasks served from cache).
        self._trace_templates: OrderedDict[str, object] = OrderedDict()

        # session_id -> set of chunk IDs already delivered (LRU over sessions)
        self._session_chunks: OrderedDict[str, set[str]] = OrderedDict()
        self._session_lock = threading.Lock()

    def compress(self, inp: CompressionInput) -> CompressionOutput:
        """Compress tool output under token budget.

        Fail-open by construction: outputs too small to matter, vague
        task contexts, insufficient savings, timeouts, and internal
        errors all return the raw output rather than raising.

        Args:
            inp: Compression input with tool output and context.

        Returns:
            Compression output with compressed content and metadata.
        """
        original_tokens = self.tokenizer.count(inp.raw_output)

        if original_tokens < 1000:
            return self._passthrough(inp, original_tokens, chunks_total=1)

        cache_key = self._cache_key(inp)
        cached = self._cache_get(cache_key, inp)
        if cached is not None:
            return cached

        # Encoder loading (potentially seconds on first use) must not
        # count against the compression deadline.
        self._ensure_warm()

        future = self._executor.submit(self._compress_inner, inp, original_tokens)
        try:
            output = future.result(timeout=self.config.hard_timeout_ms / 1000.0)
        except FutureTimeoutError:
            logger.warning(
                "Compression of %s exceeded hard timeout (%dms); returning raw output",
                inp.tool_name, self.config.hard_timeout_ms,
            )
            return self._passthrough(inp, original_tokens, chunks_total=0)
        except Exception:
            logger.exception("Compression failed; returning raw output")
            return self._passthrough(inp, original_tokens, chunks_total=0)

        if output.compression_ratio < 1.0:
            self._cache_put(cache_key, output)
        return output

    def _compress_inner(
        self, inp: CompressionInput, original_tokens: int
    ) -> CompressionOutput:
        """The actual compression flow, run under the deadline."""
        start_time = time.monotonic()
        budget = inp.budget_tokens or self.config.default_budget_tokens

        content = self._prefilter(inp.raw_output, original_tokens)

        chunks = self._chunk(content, inp.tool_args)
        if not chunks:
            return self._passthrough(inp, original_tokens, chunks_total=0)

        chunks = self._cap_chunks(chunks)

        # IDs are content hashes: identical content (repeated snippets,
        # split segments) collapses to one chunk so counts stay
        # consistent with the dependency graph and trace records.
        seen_ids: set[str] = set()
        unique_chunks: list[Chunk] = []
        for chunk in chunks:
            if chunk.id not in seen_ids:
                seen_ids.add(chunk.id)
                unique_chunks.append(chunk)
        chunks = unique_chunks

        # A single chunk larger than the whole budget can never be
        # selected; split such chunks so part of them can survive
        # (pathological inputs: one giant line, huge unparseable blob).
        chunks = self._split_oversized(chunks, budget)

        # Inter-call dedup: drop chunks this session has already seen.
        dedup_dropped: list[Chunk] = []
        if self.config.session_dedup_enabled and inp.session_id:
            chunks, dedup_dropped = self._split_delivered(inp.session_id, chunks)
            if not chunks:
                return CompressionOutput(
                    compressed_output=_DEDUP_NOTICE,
                    original_tokens=original_tokens,
                    compressed_tokens=self.tokenizer.count(_DEDUP_NOTICE),
                    compression_ratio=(
                        self.tokenizer.count(_DEDUP_NOTICE) / original_tokens
                    ),
                    chunks_selected=0,
                    chunks_total=len(dedup_dropped),
                )

        chunk_store = {c.id: c for c in chunks}
        dep_graph = DependencyGraph()
        for chunk in chunks:
            dep_graph.add_chunk(chunk)

        task_context = TaskContext(
            task_description=inp.task_description,
            tool_name=inp.tool_name,
            tool_args=inp.tool_args,
            recent_steps=inp.recent_steps,
        )

        scored_chunks = self.scorer.score_chunks(chunks, task_context)
        score_map = {sc.chunk.id: sc.score for sc in scored_chunks}

        # Failure mode: a vague task description gives near-uniform
        # scores. Truly degenerate (zero-variance) scoring makes
        # selection arbitrary — fail open. Merely flat distributions
        # often still rank the right chunks first, so those proceed but
        # the trace is flagged low_signal for the dashboard to surface.
        signal = self._signal_quality(scored_chunks)
        if signal == "none":
            logger.warning(
                "Uniform relevance scores for tool %s (task too vague?); "
                "returning uncompressed output", inp.tool_name,
            )
            trace_id = self._record_trace(
                inp, chunks, chunks, original_tokens, original_tokens,
                score_map, low_signal=True,
            )
            return CompressionOutput(
                compressed_output=inp.raw_output,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                chunks_selected=len(chunks),
                chunks_total=len(chunks),
                trace_id=trace_id,
            )
        low_signal = signal == "low"
        if low_signal:
            logger.info(
                "Low score variance for tool %s; compressing anyway "
                "(trace flagged low_signal)", inp.tool_name,
            )

        scored_chunks = self.guideline_adjuster.apply_guidelines(
            scored_chunks, inp.tool_name
        )

        selected = self.extractor.extract(
            scored_chunks, budget, dep_graph,
            lambda cid: chunk_store[cid].token_count if cid in chunk_store else 0,
        )

        # An empty selection means nothing fit the budget. Returning ""
        # would silently destroy the tool output — fail open instead.
        if not selected:
            logger.warning(
                "No chunks fit budget %d for tool %s; returning raw output",
                budget, inp.tool_name,
            )
            trace_id = self._record_trace(
                inp, chunks, chunks, original_tokens, original_tokens,
                score_map, low_signal=low_signal,
            )
            return CompressionOutput(
                compressed_output=inp.raw_output,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                chunks_selected=len(chunks),
                chunks_total=len(chunks),
                trace_id=trace_id,
            )

        if self.config.enable_coherence_validation:
            validated = self.validator.validate(selected, budget, dep_graph)
            selected = validated.chunks

        compressed_output = self._reconstruct_output(selected)
        compressed_tokens = self.tokenizer.count(compressed_output)
        ratio = compressed_tokens / original_tokens if original_tokens > 0 else 1.0

        # min_compression_ratio is the minimum *savings* worth keeping:
        # with the default of 0.3, output above 70% of the original size
        # isn't worth the coherence risk — return the original instead.
        if ratio > 1.0 - self.config.min_compression_ratio:
            trace_id = self._record_trace(
                inp, chunks, chunks, original_tokens, original_tokens,
                score_map, low_signal=low_signal,
            )
            return CompressionOutput(
                compressed_output=inp.raw_output,
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compression_ratio=1.0,
                chunks_selected=1,
                chunks_total=len(chunks),
                trace_id=trace_id,
            )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        if elapsed_ms > self.config.max_overhead_ms:
            logger.warning(
                f"Compression exceeded time budget: {elapsed_ms:.1f}ms > "
                f"{self.config.max_overhead_ms}ms"
            )

        if self.config.session_dedup_enabled and inp.session_id:
            self._mark_delivered(inp.session_id, selected)

        trace_id = self._record_trace(
            inp, chunks, selected, original_tokens, compressed_tokens,
            score_map, low_signal=low_signal,
        )

        return CompressionOutput(
            compressed_output=compressed_output,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compression_ratio=ratio,
            chunks_selected=len(selected),
            chunks_total=len(chunks) + len(dedup_dropped),
            trace_id=trace_id,
            chunk_types_selected=[c.chunk_type.value for c in selected],
        )

    # ------------------------------------------------------------------
    # Fail-open / bounding helpers

    def _passthrough(
        self, inp: CompressionInput, original_tokens: int, chunks_total: int
    ) -> CompressionOutput:
        """Return the raw output unchanged."""
        return CompressionOutput(
            compressed_output=inp.raw_output,
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            compression_ratio=1.0,
            chunks_selected=min(1, chunks_total),
            chunks_total=chunks_total,
        )

    def _ensure_warm(self) -> None:
        """Load the scorer's encoder outside the compression deadline."""
        if self._warmed_up:
            return
        with self._warmup_lock:
            if not self._warmed_up:
                try:
                    self.scorer.warmup()
                finally:
                    self._warmed_up = True

    def _prefilter(self, content: str, original_tokens: int) -> str:
        """Truncate enormous outputs to head + tail before chunking.

        The head and tail carry the structure most likely to matter
        (imports/headers and summaries/errors respectively).
        """
        half = self.config.prefilter_half_tokens
        if half <= 0 or original_tokens <= 2 * half:
            return content

        head = self.tokenizer.truncate(content, half)
        tail_chars = len(content) - len(head)
        # Approximate the tail window by characters-per-token of the head.
        chars_per_token = max(1.0, len(head) / half)
        tail = content[-int(half * chars_per_token):] if tail_chars > 0 else ""
        marker = "\n\n[... ContextMesh pre-filter: middle truncated ...]\n\n"
        logger.warning(
            "Pre-filtered giant output: %d tokens -> head+tail of ~%d",
            original_tokens, 2 * half,
        )
        return head + marker + tail

    def _cap_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Coarsen chunking by merging adjacent chunks over the cap."""
        cap = self.config.max_chunks
        if cap <= 0 or len(chunks) <= cap:
            return chunks

        group_size = -(-len(chunks) // cap)  # ceil division
        logger.warning(
            "Chunk cap: coarsening %d chunks by grouping %d adjacent",
            len(chunks), group_size,
        )
        merged: list[Chunk] = []
        ordered = sorted(chunks, key=lambda c: c.start_pos)
        for i in range(0, len(ordered), group_size):
            group = ordered[i : i + group_size]
            if len(group) == 1:
                merged.append(group[0])
                continue
            content = "\n".join(c.content for c in group)
            merged.append(
                Chunk(
                    id=Chunk.compute_id(content),
                    content=content,
                    format=group[0].format,
                    chunk_type=group[0].chunk_type,
                    token_count=sum(c.token_count for c in group),
                    start_pos=group[0].start_pos,
                    dependencies=(),
                    metadata={"coarsened_from": len(group)},
                )
            )
        return merged

    # ------------------------------------------------------------------
    # Result cache

    def _cache_key(self, inp: CompressionInput) -> str:
        material = "|".join(
            (
                hashlib.sha256(inp.raw_output.encode()).hexdigest(),
                inp.tool_name,
                json.dumps(inp.tool_args, sort_keys=True, default=str),
                inp.task_description,
                "|".join(inp.recent_steps[-3:]),
                str(inp.budget_tokens or self.config.default_budget_tokens),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def _cache_get(
        self, key: str, inp: CompressionInput
    ) -> CompressionOutput | None:
        if self.config.result_cache_size <= 0:
            return None
        with self._cache_lock:
            output = self._result_cache.get(key)
            if output is None:
                return None
            self._result_cache.move_to_end(key)
            output = replace(output)
            template = (
                self._trace_templates.get(output.trace_id)
                if output.trace_id
                else None
            )

        new_trace_id = self._record_cache_hit_trace(template, inp)
        if new_trace_id is not None:
            output = replace(output, trace_id=new_trace_id)
        return output

    def _record_cache_hit_trace(self, template: object, inp: CompressionInput) -> str | None:
        """Record a fresh trace for a cache-served call.

        A cache hit is still a real tool call: session stats must count
        it and the ACON loop must be able to find its trace by task_id.
        """
        if template is None or self.trace_store is None:
            return None
        try:
            import dataclasses
            import uuid
            from datetime import datetime

            new_trace = dataclasses.replace(
                template,  # type: ignore[type-var]
                id=str(uuid.uuid4()),
                session_id=inp.session_id,
                task_id=inp.task_id,
                created_at=datetime.now(UTC).isoformat(),
            )
            self.trace_store.record(new_trace)
            return new_trace.id
        except Exception:
            logger.exception("Failed to record cache-hit trace")
            return None

    def _cache_put(self, key: str, output: CompressionOutput) -> None:
        if self.config.result_cache_size <= 0:
            return
        with self._cache_lock:
            self._result_cache[key] = output
            self._result_cache.move_to_end(key)
            while len(self._result_cache) > self.config.result_cache_size:
                _, evicted = self._result_cache.popitem(last=False)
                if evicted.trace_id:
                    self._trace_templates.pop(evicted.trace_id, None)

    # ------------------------------------------------------------------
    # Inter-call session dedup

    def _split_delivered(
        self, session_id: str, chunks: list[Chunk]
    ) -> tuple[list[Chunk], list[Chunk]]:
        """Partition chunks into (fresh, already delivered this session)."""
        with self._session_lock:
            delivered = self._session_chunks.get(session_id, set())
        fresh = [c for c in chunks if c.id not in delivered]
        dropped = [c for c in chunks if c.id in delivered]
        return fresh, dropped

    def _mark_delivered(self, session_id: str, selected: list[Chunk]) -> None:
        with self._session_lock:
            if session_id not in self._session_chunks:
                self._session_chunks[session_id] = set()
                while len(self._session_chunks) > 256:
                    self._session_chunks.popitem(last=False)
            self._session_chunks.move_to_end(session_id)
            bucket = self._session_chunks[session_id]
            bucket.update(c.id for c in selected)
            if len(bucket) > 20000:
                bucket.clear()

    # ------------------------------------------------------------------
    # Chunking / detection

    def _chunk(self, content: str, tool_args: dict | None = None) -> list[Chunk]:
        """Chunk content using appropriate chunker.

        Args:
            content: Raw tool output.
            tool_args: Tool call arguments; a path-like argument is used
                as a language hint for code chunking.

        Returns:
            List of chunks.
        """
        detected_format = self._detect_format(content)

        if detected_format == ChunkFormat.CODE:
            chunker = CodeChunker(self._infer_language(tool_args))
            return chunker.chunk(content)
        elif detected_format == ChunkFormat.JSON:
            return JSONChunker().chunk(content)
        elif detected_format == ChunkFormat.HTML:
            return HTMLChunker().chunk(content)
        elif detected_format == ChunkFormat.MARKDOWN:
            return MarkdownChunker().chunk(content)
        elif detected_format == ChunkFormat.LOG:
            return LogChunker().chunk(content)
        elif detected_format == ChunkFormat.CSV:
            return CSVChunker().chunk(content)
        elif detected_format == ChunkFormat.SHELL:
            return ShellChunker().chunk(content)
        else:
            return self.chunker.chunk(content)

    def _infer_language(self, tool_args: dict | None) -> str:
        """Infer source language from a path-like tool argument.

        Args:
            tool_args: Tool call arguments.

        Returns:
            Language identifier, defaulting to "python".
        """
        from contextmesh.core.chunker.code_chunker import language_for_path

        for key in ("path", "file", "file_path", "filename", "uri"):
            value = (tool_args or {}).get(key)
            if isinstance(value, str):
                language = language_for_path(value)
                if language:
                    return language
        return "python"

    def _detect_format(self, content: str) -> ChunkFormat:
        """Detect output format from content.

        Args:
            content: Raw tool output.

        Returns:
            Detected ChunkFormat.
        """
        import re

        stripped = content.strip()

        if stripped.startswith("{") or stripped.startswith("["):
            return ChunkFormat.JSON

        if stripped.startswith("<"):
            if any(
                stripped.lower().startswith(tag)
                for tag in ["<html", "<!doctype", "<div", "<article", "<section", "<main"]
            ):
                return ChunkFormat.HTML

        code_indicators = [
            "def ", "class ", "function ", "import ", "const ", "let ", "var ",
            "public ", "private ", "async ", "await ", "package ", "use ", "fn ",
        ]
        if any(stripped.startswith(ind) for ind in code_indicators):
            return ChunkFormat.CODE

        # Files often open with comments/docstrings; look for definition
        # keywords at line starts anywhere in the first chunk of content.
        head = content[:4000]
        if re.search(
            r"^(def |class |import |from \w+ import |export |func |fn |pub fn )",
            head, re.MULTILINE,
        ):
            return ChunkFormat.CODE

        if re.search(r"^\$ ", content, re.MULTILINE):
            return ChunkFormat.SHELL

        if self._looks_like_csv(content):
            return ChunkFormat.CSV

        # Markdown: headings or fenced code blocks at line starts.
        if re.search(r"^#{1,6} \S", head, re.MULTILINE) or "\n```" in head:
            return ChunkFormat.MARKDOWN

        log_patterns = [
            r"^\d{4}-\d{2}-\d{2}",
            r"^\d{2}/\d{2}/\d{4}",
            r"\bINFO\b",
            r"\bERROR\b",
            r"\bWARN\b",
        ]
        if any(re.search(p, content, re.MULTILINE) for p in log_patterns):
            return ChunkFormat.LOG

        return ChunkFormat.TEXT

    def _split_oversized(self, chunks: list[Chunk], budget: int) -> list[Chunk]:
        """Split chunks that could never fit the call budget.

        Splits by lines (or by character windows for single-line blobs)
        into pieces of at most half the budget, preserving order and
        chunk type.
        """
        limit = max(budget // 2, 200)
        if all(c.token_count <= budget for c in chunks):
            return chunks

        result: list[Chunk] = []
        for chunk in chunks:
            if chunk.token_count <= budget:
                result.append(chunk)
                continue

            pieces = self.tokenizer.split_tokens(chunk.content, limit)
            for i, piece in enumerate(pieces):
                if not piece.strip():
                    continue
                result.append(
                    Chunk(
                        id=Chunk.compute_id(piece),
                        content=piece,
                        format=chunk.format,
                        chunk_type=chunk.chunk_type,
                        token_count=self.tokenizer.count(piece),
                        start_pos=chunk.start_pos + i,
                        dependencies=chunk.dependencies,
                        metadata={**chunk.metadata, "oversize_part": i},
                    )
                )
        return result

    @staticmethod
    def _looks_like_csv(content: str) -> bool:
        """Detect delimiter-separated tabular data.

        CSV means: several lines, all sharing the same nonzero count of
        a consistent delimiter (comma or tab). Prose with occasional
        commas fails the consistency requirement.
        """
        lines = [ln for ln in content.strip().splitlines()[:10] if ln.strip()]
        if len(lines) < 3:
            return False

        for delimiter in (",", "\t"):
            counts = [ln.count(delimiter) for ln in lines]
            if counts[0] >= 1 and all(c == counts[0] for c in counts):
                return True
        return False

    # ------------------------------------------------------------------
    # Scoring / tracing helpers

    def _signal_quality(self, scored_chunks: list[ScoredChunk]) -> str:
        """Classify relevance-score signal: "ok", "low", or "none".

        "none" (exactly uniform scores) means selection would be
        arbitrary — the caller fails open. "low" (spec failure mode 2:
        std below threshold) still usually ranks the right chunks first,
        so compression proceeds with the trace flagged for review.
        """
        if len(scored_chunks) < 4:
            return "ok"

        scores = [sc.score for sc in scored_chunks]
        std = statistics.pstdev(scores)
        if std == 0:
            return "none"
        if std < self.config.low_signal_std_threshold:
            return "low"
        return "ok"

    def _record_trace(
        self,
        inp: CompressionInput,
        chunks: list[Chunk],
        selected: list[Chunk],
        original_tokens: int,
        compressed_tokens: int,
        score_map: dict[str, float] | None = None,
        low_signal: bool = False,
    ) -> str | None:
        """Record a compression trace if a trace store is attached.

        Never raises — trace persistence must not block compression.

        Returns:
            The trace ID, or None when tracing is disabled or failed.
        """
        if self.trace_store is None:
            return None
        try:
            from contextmesh.feedback.trace_store import CompressionTrace

            selected_ids = {c.id for c in selected}
            pruned = [c for c in chunks if c.id not in selected_ids]
            args_hash = hashlib.sha256(
                json.dumps(inp.tool_args, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]

            metadata: dict = {}
            preview_chars = self.config.trace_preview_chars
            if 0 < preview_chars and len(chunks) <= self.config.trace_preview_max_chunks:
                metadata["chunk_previews"] = {
                    c.id: c.content[:preview_chars] for c in chunks
                }
                metadata["chunk_token_counts"] = {c.id: c.token_count for c in chunks}
                if score_map:
                    metadata["chunk_scores"] = {
                        c.id: round(score_map.get(c.id, 0.0), 4) for c in chunks
                    }

            trace = CompressionTrace(
                session_id=inp.session_id,
                task_id=inp.task_id,
                tool_name=inp.tool_name,
                tool_args_hash=args_hash,
                chunk_ids_selected=[c.id for c in selected],
                chunk_ids_pruned=[c.id for c in pruned],
                original_token_count=original_tokens,
                compressed_token_count=compressed_tokens,
                compression_ratio=(
                    compressed_tokens / original_tokens if original_tokens else 1.0
                ),
                chunk_types_selected=[c.chunk_type.value for c in selected],
                chunk_types_pruned=[c.chunk_type.value for c in pruned],
                low_signal=low_signal,
                metadata=metadata,
            )
            self.trace_store.record(trace)
            with self._cache_lock:
                self._trace_templates[trace.id] = trace
                while len(self._trace_templates) > max(
                    16, 2 * self.config.result_cache_size
                ):
                    self._trace_templates.popitem(last=False)
            return trace.id
        except Exception:
            logger.exception("Failed to record compression trace")
            return None

    def _reconstruct_output(self, chunks: list[Chunk]) -> str:
        """Reconstruct compressed output from chunks.

        Args:
            chunks: Selected chunks.

        Returns:
            Reconstructed text content.
        """
        if not chunks:
            return ""

        sorted_chunks = sorted(chunks, key=lambda c: c.start_pos)

        # JSON chunks are each {"<path>": <value>} objects; merge them into
        # a single valid JSON document instead of concatenating fragments.
        if all(c.format == ChunkFormat.JSON for c in sorted_chunks):
            merged: dict = {}
            parseable = True
            for c in sorted_chunks:
                try:
                    obj = json.loads(c.content)
                except json.JSONDecodeError:
                    parseable = False
                    break
                if isinstance(obj, dict):
                    merged.update(obj)
                else:
                    parseable = False
                    break
            if parseable and merged:
                return json.dumps(merged, ensure_ascii=False, indent=1)

        return "\n".join(c.content for c in sorted_chunks)
