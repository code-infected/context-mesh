"""CSV row chunker.

Segments CSV data using sliding window and category grouping.
Header row is included in every chunk. For structured data with
low-cardinality columns, groups by category value.

Architecture:
    CSV text -> header detection -> cardinality analysis ->
    row grouping (sliding window or category) -> chunks with headers
"""

from __future__ import annotations

from typing import ClassVar

from contextmesh.core.chunker.base import (
    Chunk,
    ChunkerBase,
    ChunkerError,
    ChunkFormat,
    ChunkType,
)
from contextmesh.core.tokenizer import TokenCounter


class CSVChunker(ChunkerBase):
    """Sliding window and category-based CSV chunker.

    Groups CSV rows into chunks using either a sliding window approach
    or category-based grouping when a low-cardinality column is detected.
    Header row is always included in every chunk.

    Attributes:
        rows_per_chunk: Number of rows per chunk for sliding window.
        group_by_cardinality_threshold: Max unique values for category grouping.

    Example:
        >>> chunker = CSVChunker(rows_per_chunk=50)
        >>> csv_data = '''name,age,city
        ... Alice,30,NYC
        ... Bob,25,LA'''
        >>> chunks = chunker.chunk(csv_data)
    """

    format: ClassVar[ChunkFormat] = ChunkFormat.CSV

    def __init__(
        self,
        rows_per_chunk: int = 50,
        group_by_cardinality_threshold: int = 20,
    ) -> None:
        """Initialize CSV chunker.

        Args:
            rows_per_chunk: Rows per chunk for sliding window.
            group_by_cardinality_threshold: Max unique values for category grouping.
        """
        self.rows_per_chunk = rows_per_chunk
        self.cardinality_threshold = group_by_cardinality_threshold
        self._tokenizer = TokenCounter.get_default()

    def chunk(self, content: str) -> list[Chunk]:
        """Segment CSV into row groups.

        Args:
            content: CSV text to chunk.

        Returns:
            List of CSV chunks with headers.

        Raises:
            ChunkerError: If CSV parsing fails.
        """
        if not content.strip():
            return []

        try:
            lines = content.strip().split("\n")
            if len(lines) < 2:
                return []

            header = lines[0]
            rows = lines[1:]

            if not rows:
                return []

        except Exception as e:
            raise ChunkerError(f"Failed to parse CSV: {e}", format=self.format) from e

        category_col = self._detect_category_column(header, rows)

        if category_col is not None:
            chunks = self._chunk_by_category(header, rows, category_col)
        else:
            chunks = self._chunk_by_window(header, rows)

        return self._sort_and_assign_positions(chunks)

    def _detect_category_column(self, header: str, rows: list[str]) -> int | None:
        """Detect if a column has low cardinality for category grouping.

        Args:
            header: CSV header line.
            rows: Data rows.

        Returns:
            Column index for category grouping, or None.
        """
        import csv
        import io

        reader = csv.reader(io.StringIO(header))
        header_cols = next(reader)
        num_cols = len(header_cols)

        if num_cols == 0:
            return None

        column_values: list[set[str]] = [set() for _ in range(num_cols)]

        sample_rows = rows[:100]
        for row in sample_rows:
            reader = csv.reader(io.StringIO(row))
            try:
                values = next(reader)
                for i, val in enumerate(values):
                    if i < num_cols:
                        column_values[i].add(val.strip())
            except StopIteration:
                continue

        for i, values in enumerate(column_values):
            if 1 < len(values) <= self.cardinality_threshold:
                return i

        return None

    def _chunk_by_category(
        self,
        header: str,
        rows: list[str],
        category_col: int,
    ) -> list[Chunk]:
        """Chunk rows by category value.

        Args:
            header: CSV header.
            rows: Data rows.
            category_col: Column to group by.

        Returns:
            List of category-grouped chunks.
        """
        import csv
        import io

        categories: dict[str, list[str]] = {}

        for row in rows:
            reader = csv.reader(io.StringIO(row))
            try:
                values = next(reader)
                if category_col < len(values):
                    category = values[category_col].strip()
                    if category not in categories:
                        categories[category] = []
                    categories[category].append(row)
            except StopIteration:
                continue

        chunks: list[Chunk] = []
        for category, category_rows in categories.items():
            chunk_content = header + "\n" + "\n".join(category_rows)
            chunks.append(
                Chunk(
                    id=Chunk.compute_id(chunk_content),
                    content=chunk_content,
                    format=ChunkFormat.CSV,
                    chunk_type=ChunkType.CSV_ROWS,
                    token_count=self._tokenizer.count(chunk_content),
                    start_pos=0,
                    dependencies=(),
                    metadata={
                        "category_column": category_col,
                        "category_value": category,
                        "num_rows": len(category_rows),
                    },
                )
            )

        return chunks

    def _chunk_by_window(self, header: str, rows: list[str]) -> list[Chunk]:
        """Chunk rows using sliding window.

        Args:
            header: CSV header.
            rows: Data rows.

        Returns:
            List of windowed chunks.
        """
        chunks: list[Chunk] = []
        num_chunks = (len(rows) + self.rows_per_chunk - 1) // self.rows_per_chunk

        for i in range(num_chunks):
            start_idx = i * self.rows_per_chunk
            end_idx = min(start_idx + self.rows_per_chunk, len(rows))
            window_rows = rows[start_idx:end_idx]

            chunk_content = header + "\n" + "\n".join(window_rows)
            chunks.append(
                Chunk(
                    id=Chunk.compute_id(chunk_content),
                    content=chunk_content,
                    format=ChunkFormat.CSV,
                    chunk_type=ChunkType.CSV_ROWS,
                    token_count=self._tokenizer.count(chunk_content),
                    start_pos=0,
                    dependencies=(),
                    metadata={
                        "start_row": start_idx + 1,
                        "end_row": end_idx,
                        "num_rows": len(window_rows),
                    },
                )
            )

        return chunks

    def _sort_and_assign_positions(self, chunks: list[Chunk]) -> list[Chunk]:
        """Sort chunks and assign byte positions.

        Args:
            chunks: Unsorted chunks.

        Returns:
            Chunks sorted by category/position.
        """
        sorted_chunks = sorted(chunks, key=lambda c: c.metadata.get("category_value", ""))

        offset = 0
        result: list[Chunk] = []

        for chunk in sorted_chunks:
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
