"""Log output chunker.

Segments structured and unstructured log output into event groups.
Groups lines by timestamp proximity, log level, and component prefix.
Handles JSON logs, Python tracebacks, and raw text logs.

Architecture:
    Log text -> line parsing -> event grouping ->
    timestamp/log-level/component grouping -> chunks
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkFormat,
    ChunkType,
)
from contextmesh.core.tokenizer import TokenCounter

LOG_LEVEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "TRACE": re.compile(r"\bTRACE\b", re.IGNORECASE),
    "DEBUG": re.compile(r"\bDEBUG\b", re.IGNORECASE),
    "INFO": re.compile(r"\bINFO\b", re.IGNORECASE),
    "WARN": re.compile(r"\b(WARN|WARNING)\b", re.IGNORECASE),
    "ERROR": re.compile(r"\bERROR\b", re.IGNORECASE),
    "FATAL": re.compile(r"\b(FATAL|CRITICAL)\b", re.IGNORECASE),
}

TIMESTAMP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"),
    re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}"),
    re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),
    re.compile(r"^\w{3} \d{1,2}, \d{4} \d{2}:\d{2}:\d{2}"),
]

COMPONENT_PATTERN = re.compile(r"^([\w\.]+):")


@dataclass
class LogEvent:
    """A grouped log event.

    Attributes:
        lines: Lines belonging to this event.
        start_line: First line number.
        level: Detected log level (if any).
        component: Detected component (if any).
        timestamp: Detected timestamp (if any).
        is_traceback: Whether this is a Python traceback.
    """

    lines: list[str]
    start_line: int
    level: str | None = None
    component: str | None = None
    timestamp: str | None = None
    is_traceback: bool = False


class LogChunker(ChunkerBase):
    """Event-grouping log output chunker.

    Groups log lines into events based on timestamp proximity,
    log level changes, and component boundaries. Handles
    structured (JSON) and unstructured logs.

    Attributes:
        event_window_ms: Group lines within this time window.
        max_lines_per_chunk: Maximum lines before forced split.

    Example:
        >>> chunker = LogChunker(event_window_ms=100, max_lines_per_chunk=20)
        >>> logs = '''2024-01-01 10:00:00 INFO Starting
        ... 2024-01-01 10:00:01 INFO Processing
        ... 2024-01-01 10:00:05 ERROR Failed'''
        >>> chunks = chunker.chunk(logs)
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.LOG

    def __init__(
        self,
        event_window_ms: int = 100,
        max_lines_per_chunk: int = 20,
    ) -> None:
        """Initialize log chunker.

        Args:
            event_window_ms: Milliseconds within which lines are grouped.
            max_lines_per_chunk: Maximum lines per event chunk.
        """
        self.event_window_ms = event_window_ms
        self.max_lines_per_chunk = max_lines_per_chunk
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment log output into event groups.

        Args:
            content: Log text to chunk.

        Returns:
            List of log event chunks.

        Raises:
            ChunkerError: If log parsing fails completely.
        """
        if not content.strip():
            return []

        lines = content.split("\n")
        events = self._group_lines(lines)

        chunks: list[Chunk] = []
        for event in events:
            chunk = self._event_to_chunk(event)
            if chunk:
                chunks.append(chunk)

        return self._post_process(chunks)

    def _group_lines(self, lines: list[str]) -> list[LogEvent]:
        """Group log lines into events.

        Args:
            lines: Individual log lines.

        Returns:
            List of grouped log events.
        """
        events: list[LogEvent] = []
        current_event: LogEvent | None = None

        for i, line in enumerate(lines):
            if not line.strip():
                if current_event:
                    current_event.lines.append(line)
                continue

            parsed = self._parse_line(line, i)

            if parsed.is_traceback:
                if current_event:
                    current_event.lines.append(line)
                    if "Traceback" in line:
                        current_event.is_traceback = True
                continue

            should_start_new = True

            if current_event:
                if self._same_event(current_event, parsed):
                    should_start_new = False

            if should_start_new:
                if current_event:
                    events.append(current_event)
                current_event = parsed
            else:
                current_event.lines.append(line)
                if parsed.level:
                    current_event.level = parsed.level
                if parsed.component:
                    current_event.component = parsed.component

        if current_event:
            events.append(current_event)

        return events

    def _parse_line(self, line: str, line_idx: int) -> LogEvent:
        """Parse a single log line.

        Args:
            line: Log line to parse.
            line_idx: Line number (0-indexed).

        Returns:
            Parsed log event metadata.
        """
        event = LogEvent(lines=[line], start_line=line_idx)

        if "Traceback" in line or line.strip().startswith("File "):
            event.is_traceback = True
            return event

        for level, pattern in LOG_LEVEL_PATTERNS.items():
            if pattern.search(line):
                event.level = level
                break

        comp_match = COMPONENT_PATTERN.match(line.strip())
        if comp_match:
            event.component = comp_match.group(1)

        for ts_pattern in TIMESTAMP_PATTERNS:
            ts_match = ts_pattern.match(line.strip())
            if ts_match:
                event.timestamp = ts_match.group(0)
                break

        try:
            json_start = line.index("{")
            if json_start >= 0 and line.strip().endswith(("}", "]")):
                json_str = line[json_start:]
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    if "level" in parsed:
                        event.level = parsed["level"].upper()
                    if "timestamp" in parsed:
                        event.timestamp = str(parsed["timestamp"])
                    if "logger" in parsed or "name" in parsed:
                        event.component = parsed.get("logger") or parsed.get("name")
        except (ValueError, json.JSONDecodeError):
            pass

        return event

    def _same_event(self, current: LogEvent, incoming: LogEvent) -> bool:
        """Check if incoming line belongs to current event.

        Args:
            current: Current event.
            incoming: Incoming line metadata.

        Returns:
            True if same event, False otherwise.
        """
        if incoming.is_traceback:
            return True

        if current.is_traceback and incoming.level is None:
            return True

        if incoming.component and current.component:
            if incoming.component != current.component:
                return False

        if incoming.level and current.level:
            if incoming.level != current.level:
                return False

        if current.level and not incoming.level:
            if len(current.lines) >= self.max_lines_per_chunk:
                return False

        return True

    def _event_to_chunk(self, event: LogEvent) -> Chunk | None:
        """Convert a log event to a Chunk.

        Args:
            event: Log event data.

        Returns:
            Chunk or None if event is empty.
        """
        if not event.lines:
            return None

        content = "\n".join(event.lines)

        if event.is_traceback:
            chunk_type = ChunkType.LOG_TRACE
        elif event.level in ("ERROR", "FATAL", "CRITICAL"):
            chunk_type = ChunkType.LOG_ERROR
        else:
            chunk_type = ChunkType.LOG_EVENT

        return Chunk(
            id=Chunk.compute_id(content),
            content=content,
            format=ChunkFormat.LOG,
            chunk_type=chunk_type,
            token_count=self._tokenizer.count(content),
            start_pos=0,
            dependencies=[],
            metadata={
                "level": event.level,
                "component": event.component,
                "timestamp": event.timestamp,
                "is_traceback": event.is_traceback,
                "num_lines": len(event.lines),
            },
        )

    def _post_process(self, chunks: list[Chunk]) -> list[Chunk]:
        """Post-process log chunks.

        Args:
            chunks: Initial chunks.

        Returns:
            Processed chunks with fixed positions.
        """
        offset = 0
        result: list[Chunk] = []

        for chunk in chunks:
            new_chunk = Chunk(
                id=chunk.id,
                content=chunk.content,
                format=chunk.format,
                chunk_type=chunk.chunk_type,
                token_count=chunk.token_count,
                start_pos=offset,
                dependencies=chunk.dependencies,
                metadata=chunk.metadata,
            )
            result.append(new_chunk)
            offset += len(chunk.content) + 1

        return result
