"""Token counting utilities using tiktoken.

Tiktoken is OpenAI's fast BPE tokenizer. We use it for accurate
token counting since it matches the tokenizers used by major LLM providers
(GPT-4, Claude, etc.), making our token budgets accurate for real-world use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tiktoken

if TYPE_CHECKING:
    from tiktoken import Encoding


class TokenCounter:
    """Fast token counter using tiktoken.

    Provides accurate token counting for any text content. Uses
    cl100k_base encoding by default (used by GPT-4 and Claude).

    Attributes:
        encoding: The underlying tiktoken encoding instance.
        encoding_name: Name of the encoding for debugging.

    Example:
        >>> counter = TokenCounter()
        >>> tokens = counter.count("Hello, world!")
        >>> tokens == counter.count(["Hello", ", ", "world!"])
        True
    """

    _default_encoding: Encoding | None = None
    _encoding_name: str = "cl100k_base"

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        """Initialize token counter with specified encoding.

        Args:
            encoding_name: Tiktoken encoding to use. Defaults to cl100k_base.
                Common options: cl100k_base (GPT-4, Claude), p50k_base (GPT-3.5),
                r50k_base (GPT-3).
        """
        self._encoding_name = encoding_name
        self.encoding = tiktoken.get_encoding(encoding_name)

    @classmethod
    def get_default(cls) -> TokenCounter:
        """Get a singleton default token counter.

        Avoids repeated initialization overhead for the common case.

        Returns:
            A TokenCounter instance using cl100k_base encoding.
        """
        if cls._default_encoding is None:
            cls._default_encoding = cls()
        return cls._default_encoding

    def count(self, text: str | list[str]) -> int:
        """Count tokens in text or text segments.

        Handles both single strings and pre-split text segments.
        When given a list, processes each segment and returns the total.

        Args:
            text: Either a single string or a list of string segments.

        Returns:
            Total token count across all input.

        Example:
            >>> counter = TokenCounter()
            >>> counter.count("Hello, world!")
            4
            >>> counter.count(["Hello", ", ", "world!"])
            4
        """
        if isinstance(text, str):
            return len(self.encoding.encode(text))

        return sum(len(self.encoding.encode(segment)) for segment in text)

    def truncate(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within token budget.

        Ensures the returned text has at most max_tokens tokens.
        This is a safety net for cases where chunking doesn't perfectly
        respect token limits.

        Args:
            text: Text to truncate.
            max_tokens: Maximum tokens allowed.

        Returns:
            Truncated text that fits within token budget.

        Example:
            >>> counter = TokenCounter()
            >>> long_text = "a" * 10000
            >>> truncated = counter.truncate(long_text, 100)
            >>> counter.count(truncated) <= 100
            True
        """
        tokens = self.encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text

        return self.encoding.decode(tokens[:max_tokens])

    def split_tokens(
        self, text: str, max_tokens_per_chunk: int, overlap: int = 0
    ) -> list[str]:
        """Split text into token-bounded chunks.

        Provides a fallback chunking strategy when semantic chunking
        produces chunks that are too large. Used primarily for edge cases
        like very long lines in unstructured text.

        Args:
            text: Text to split.
            max_tokens_per_chunk: Maximum tokens per chunk.
            overlap: Number of overlapping tokens between chunks.

        Returns:
            List of text chunks, each within token budget.

        Example:
            >>> counter = TokenCounter()
            >>> chunks = counter.split_tokens("a b c d e", 2, overlap=1)
            >>> len(chunks)
            3
        """
        tokens = self.encoding.encode(text)
        chunks: list[str] = []

        start = 0
        while start < len(tokens):
            end = start + max_tokens_per_chunk
            chunk_tokens = tokens[start:end]
            chunks.append(self.encoding.decode(chunk_tokens))

            if overlap > 0 and end < len(tokens):
                start = end - overlap
            else:
                start = end

        return chunks

    @property
    def name(self) -> str:
        """Get the encoding name for debugging.

        Returns:
            The name of the tiktoken encoding in use.
        """
        return self._encoding_name
