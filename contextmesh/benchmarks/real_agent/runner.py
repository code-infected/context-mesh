"""Real-agent benchmark runner.

Runs the task suite under two conditions — baseline (raw tool outputs)
and contextmesh (compressed tool outputs) — and reports completion
rate, tool-result token usage, and compression overhead.

Provider-agnostic: Claude via the native Anthropic API, or any
OpenAI-compatible endpoint (OpenAI, Gemini, Groq, Mistral, OpenRouter,
DeepSeek, xAI, local Ollama/vLLM).

Usage:
    # Harness mechanics without an API key (deterministic scripted agent):
    python -m contextmesh.benchmarks.real_agent.runner --model scripted

    # Claude (requires ANTHROPIC_API_KEY):
    python -m contextmesh.benchmarks.real_agent.runner --model claude-sonnet-5

    # OpenAI (requires OPENAI_API_KEY):
    python -m contextmesh.benchmarks.real_agent.runner --model gpt-4o-mini

    # Local Ollama (no key):
    python -m contextmesh.benchmarks.real_agent.runner \\
        --model llama3.1 --base-url http://localhost:11434/v1

    # Gemini via its OpenAI-compatible endpoint:
    python -m contextmesh.benchmarks.real_agent.runner \\
        --model gemini-2.0-flash \\
        --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \\
        --api-key-env GEMINI_API_KEY

    # Options:
    #   --provider auto|anthropic|openai|scripted   (default: auto from model)
    #   --budget 1500          compression budget per tool call
    #   --conditions baseline contextmesh
    #   --output results/real_agent.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from pathlib import Path

from contextmesh.benchmarks.real_agent.agent import (
    AgentRun,
    ClaudeAgent,
    OpenAICompatAgent,
    ScriptedAgent,
)
from contextmesh.config import create_pipeline, load_config
from contextmesh.core.chunker.base import CompressionInput

logger = logging.getLogger(__name__)

TASKS_PATH = Path(__file__).parent / "tasks.json"


def load_tasks(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["tasks"]


def make_transform(condition: str, budget: int, session_id: str):
    """Build the tool-output transform for a condition."""
    if condition == "baseline":
        return lambda tool_name, raw, args: raw

    config = load_config()
    pipeline = create_pipeline(config)
    pipeline.config.hard_timeout_ms = 120000  # benchmark measures tokens, not latency
    counter = {"n": 0}

    def transform(tool_name: str, raw: str, args: dict) -> str:
        counter["n"] += 1
        out = pipeline.compress(
            CompressionInput(
                session_id=session_id,
                task_id=f"{session_id}-call-{counter['n']}",
                tool_name=tool_name,
                tool_args=args,
                raw_output=raw,
                task_description=transform.task_description,  # type: ignore[attr-defined]
                budget_tokens=budget,
            )
        )
        return out.compressed_output

    transform.task_description = ""  # type: ignore[attr-defined]
    return transform


def resolve_provider(provider: str, model: str) -> str:
    """Resolve --provider auto based on the model name."""
    if provider != "auto":
        return provider
    if model == "scripted":
        return "scripted"
    if model.startswith("claude"):
        return "anthropic"
    return "openai"


def build_agent(
    provider: str, model: str, base_url: str | None, api_key: str | None
):
    """Construct the agent for a resolved provider."""
    if provider == "scripted":
        return ScriptedAgent(model)
    if provider == "anthropic":
        return ClaudeAgent(model)
    return OpenAICompatAgent(model, base_url=base_url, api_key=api_key)


def run_condition(
    condition: str,
    tasks: list[dict],
    model: str,
    budget: int,
    provider: str = "auto",
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[dict]:
    """Run all tasks under one condition, returning row dicts."""
    provider = resolve_provider(provider, model)
    rows = []
    for task in tasks:
        transform = make_transform(condition, budget, f"bench-{condition}-{task['id']}")
        if hasattr(transform, "task_description"):
            transform.task_description = task["question"]

        agent = build_agent(provider, model, base_url, api_key)

        start = time.monotonic()
        try:
            if isinstance(agent, ScriptedAgent):
                run = agent.run(task["question"], transform, task)
            else:
                run = agent.run(task["question"], transform)
        except Exception as e:  # API errors etc. count as failures
            logger.error("Task %s errored: %s", task["id"], e)
            run = AgentRun(final_text="", error=str(e))
        elapsed = time.monotonic() - start

        success = bool(re.search(task["answer_regex"], run.final_text, re.IGNORECASE))
        reduction = (
            1 - run.delivered_tool_tokens / run.raw_tool_tokens
            if run.raw_tool_tokens
            else 0.0
        )
        rows.append(
            {
                "condition": condition,
                "task_id": task["id"],
                "success": success,
                "tool_calls": run.tool_calls,
                "raw_tool_tokens": run.raw_tool_tokens,
                "delivered_tool_tokens": run.delivered_tool_tokens,
                "tool_token_reduction": round(reduction, 4),
                "api_input_tokens": run.api_input_tokens,
                "api_output_tokens": run.api_output_tokens,
                "turns": run.turns,
                "elapsed_s": round(elapsed, 2),
                "error": run.error or "",
            }
        )
        print(
            f"  [{condition}] {task['id']}: "
            f"{'PASS' if success else 'FAIL'} "
            f"tool tokens {run.raw_tool_tokens} -> {run.delivered_tool_tokens}"
        )
    return rows


def summarize(rows: list[dict]) -> None:
    """Print the per-condition summary table."""
    conditions = sorted({r["condition"] for r in rows})
    print("\n=== Summary ===")
    header = (
        f"{'condition':<14} {'tasks':>5} {'success':>8} {'raw tok':>9} "
        f"{'delivered':>10} {'reduction':>10}"
    )
    print(header)
    for condition in conditions:
        subset = [r for r in rows if r["condition"] == condition]
        n = len(subset)
        successes = sum(r["success"] for r in subset)
        raw = sum(r["raw_tool_tokens"] for r in subset)
        delivered = sum(r["delivered_tool_tokens"] for r in subset)
        reduction = 1 - delivered / raw if raw else 0
        print(
            f"{condition:<14} {n:>5} {successes / n:>7.0%} {raw:>9} "
            f"{delivered:>10} {reduction:>9.1%}"
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="ContextMesh real-agent benchmark")
    parser.add_argument("--model", default="scripted",
                        help='"scripted" (no API key) or a provider model id')
    parser.add_argument("--provider", default="auto",
                        choices=["auto", "anthropic", "openai", "scripted"],
                        help="auto: claude* -> anthropic, else openai-compatible")
    parser.add_argument("--base-url", default=None,
                        help="OpenAI-compatible endpoint (Ollama, Gemini, Groq, ...)")
    parser.add_argument("--api-key-env", default=None,
                        help="Env var holding the provider API key "
                             "(default: ANTHROPIC_API_KEY or OPENAI_API_KEY)")
    parser.add_argument("--budget", type=int, default=1500)
    parser.add_argument("--conditions", nargs="+",
                        default=["baseline", "contextmesh"])
    parser.add_argument("--tasks", default=str(TASKS_PATH))
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    tasks = load_tasks(Path(args.tasks))
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]

    provider = resolve_provider(args.provider, args.model)

    import os

    api_key = None
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set; use --model scripted, or an "
                  "OpenAI-compatible provider via --base-url.", file=sys.stderr)
            return 2
    elif provider == "openai":
        key_env = args.api_key_env or "OPENAI_API_KEY"
        api_key = os.environ.get(key_env)
        is_local = bool(args.base_url) and (
            "localhost" in args.base_url or "127.0.0.1" in args.base_url
        )
        if not api_key and not is_local:
            print(f"{key_env} is not set. Local servers (--base-url "
                  "http://localhost:...) need no key; other providers do. "
                  "Use --api-key-env to name a different variable.",
                  file=sys.stderr)
            return 2

    all_rows: list[dict] = []
    for condition in args.conditions:
        print(f"\ncondition: {condition} "
              f"(provider={provider}, model={args.model}, budget={args.budget})")
        all_rows.extend(
            run_condition(
                condition, tasks, args.model, args.budget,
                provider=provider, base_url=args.base_url, api_key=api_key,
            )
        )

    summarize(all_rows)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
