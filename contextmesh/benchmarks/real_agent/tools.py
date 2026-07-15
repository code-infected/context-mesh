"""Executable file tools for the real-agent benchmark.

Deterministic tools over the corpus directory: list_files and
read_file. These produce the large raw outputs that compression
operates on.
"""

from __future__ import annotations

from pathlib import Path

CORPUS_DIR = Path(__file__).parent / "corpus"

TOOL_DEFINITIONS = [
    {
        "name": "list_files",
        "description": "List the files available in the workspace.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read the full content of a workspace file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File name to read"},
            },
            "required": ["path"],
        },
    },
]


def execute_tool(name: str, args: dict, corpus_dir: Path | None = None) -> str:
    """Execute a benchmark tool.

    Args:
        name: Tool name.
        args: Tool arguments.
        corpus_dir: Corpus directory override (defaults to bundled corpus).

    Returns:
        Raw tool output text.
    """
    corpus = corpus_dir or CORPUS_DIR

    if name == "list_files":
        return "\n".join(sorted(p.name for p in corpus.iterdir() if p.is_file()))

    if name == "read_file":
        target = corpus / Path(str(args.get("path", ""))).name  # no traversal
        if not target.is_file():
            return f"ERROR: file not found: {args.get('path')}"
        return target.read_text(encoding="utf-8")

    return f"ERROR: unknown tool {name}"
