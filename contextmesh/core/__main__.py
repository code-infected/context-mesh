"""ContextMesh CLI entry point.

Provides command-line interface for running compression
directly on files or text input.

Usage:
    python -m contextmesh.core.pipeline --help
    python -m contextmesh.core.pipeline --tool-name read_file \
        --input tests/fixtures/large_python_file.py \
        --task "find all authentication-related functions" \
        --budget 4000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from contextmesh.core.chunker.base import CompressionInput
from contextmesh.core.pipeline import CompressionPipeline, PipelineConfig


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for CLI.

    Args:
        level: Log level string.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        description="ContextMesh compression pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--tool-name",
        default="read_file",
        help="Tool name (e.g., read_file, web_scrape)",
    )

    parser.add_argument(
        "--input",
        type=Path,
        help="Input file path (mutually exclusive with --text)",
    )

    parser.add_argument(
        "--text",
        help="Input text directly (mutually exclusive with --input)",
    )

    parser.add_argument(
        "--task",
        required=True,
        help="Task description for relevance scoring",
    )

    parser.add_argument(
        "--budget",
        type=int,
        default=8000,
        help="Token budget for compression",
    )

    parser.add_argument(
        "--session-id",
        default="cli-session",
        help="Session ID for tracing",
    )

    parser.add_argument(
        "--task-id",
        default="cli-task",
        help="Task ID for tracing",
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Output file path (default: stdout)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Logging level",
    )

    parser.add_argument(
        "--tool-args",
        type=json.loads,
        default={},
        help="Tool arguments as JSON string",
    )

    return parser


def run_compression(args: argparse.Namespace) -> int:
    """Run compression based on CLI arguments.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    if args.input:
        try:
            raw_output = args.input.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read input file: {e}")
            return 1
    elif args.text:
        raw_output = args.text
    else:
        logger.error("Must provide either --input or --text")
        return 1

    inp = CompressionInput(
        session_id=args.session_id,
        task_id=args.task_id,
        tool_name=args.tool_name,
        tool_args=args.tool_args,
        raw_output=raw_output,
        task_description=args.task,
        budget_tokens=args.budget,
    )

    pipeline = CompressionPipeline()
    result = pipeline.compress(inp)

    if args.json:
        output_data = {
            "compressed_output": result.compressed_output,
            "original_tokens": result.original_tokens,
            "compressed_tokens": result.compressed_tokens,
            "compression_ratio": result.compression_ratio,
            "chunks_selected": result.chunks_selected,
            "chunks_total": result.chunks_total,
            "reduction_percent": (1 - result.compression_ratio) * 100,
        }
        output = json.dumps(output_data, indent=2)
    else:
        output = result.compressed_output

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        logger.info(f"Output written to {args.output}")
    else:
        print(output)

    if not args.json:
        print(
            f"\n--- Stats ---\n"
            f"Original: {result.original_tokens} tokens\n"
            f"Compressed: {result.compressed_tokens} tokens\n"
            f"Ratio: {result.compression_ratio:.3f} "
            f"({(1 - result.compression_ratio) * 100:.1f}% reduction)\n"
            f"Chunks: {result.chunks_selected}/{result.chunks_total}",
            file=sys.stderr,
        )

    return 0


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = create_parser()
    args = parser.parse_args()
    return run_compression(args)


if __name__ == "__main__":
    sys.exit(main())
