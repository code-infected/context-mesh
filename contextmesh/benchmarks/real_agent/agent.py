"""Agents for the real-agent benchmark.

Three implementations of the same tool-use loop (ContextMesh itself is
provider-agnostic — these exist only so the benchmark can drive a real
model):

ClaudeAgent      — Claude via the native Anthropic API.
                   Requires ANTHROPIC_API_KEY.

OpenAICompatAgent — any provider speaking the OpenAI chat-completions
                   protocol: OpenAI, Google Gemini, Groq, Mistral,
                   OpenRouter, DeepSeek, xAI, Azure OpenAI, or a local
                   Ollama/vLLM server. Point base_url at the provider
                   and supply its API key (local servers need none).

ScriptedAgent    — deterministic stand-in for harness testing without
                   an API key: reads the task's target file, then
                   answers only if the expected evidence survived in
                   the (possibly compressed) tool result. This makes
                   compression-caused information loss directly
                   observable, but it is NOT a substitute for real
                   completion-rate numbers.

All agents receive tool outputs through a `transform` callback — the
harness passes ContextMesh compression (or identity for baseline).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from contextmesh.benchmarks.real_agent.tools import TOOL_DEFINITIONS, execute_tool
from contextmesh.core.tokenizer import TokenCounter

logger = logging.getLogger(__name__)

Transform = Callable[[str, str, dict], str]  # (tool_name, raw_output, args) -> text

SYSTEM_PROMPT = (
    "You are a focused assistant answering questions about files in a "
    "workspace. Use the tools to inspect files. Answer concisely with "
    "the specific fact requested. If you cannot find the answer, say "
    "\"CANNOT FIND\"."
)

MAX_TURNS = 8


@dataclass
class AgentRun:
    """Result of one agent run on one task."""

    final_text: str = ""
    tool_calls: int = 0
    raw_tool_tokens: int = 0
    delivered_tool_tokens: int = 0
    api_input_tokens: int = 0
    api_output_tokens: int = 0
    turns: int = 0
    error: str | None = None
    tool_history: list[str] = field(default_factory=list)


class ClaudeAgent:
    """Tool-use loop over the native Anthropic API."""

    def __init__(self, model: str = "claude-sonnet-5", client: Any = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.model = model
        self.client = client
        self._tokenizer = TokenCounter.get_default()

    def run(self, question: str, transform: Transform) -> AgentRun:
        """Run the loop until the model answers or MAX_TURNS is hit."""
        run = AgentRun()
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

        for _ in range(MAX_TURNS):
            run.turns += 1
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )
            run.api_input_tokens += response.usage.input_tokens
            run.api_output_tokens += response.usage.output_tokens

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            texts = [b.text for b in response.content if b.type == "text"]

            if not tool_uses:
                run.final_text = "\n".join(texts)
                return run

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                raw = execute_tool(block.name, dict(block.input))
                delivered = transform(block.name, raw, dict(block.input))
                run.tool_calls += 1
                run.raw_tool_tokens += self._tokenizer.count(raw)
                run.delivered_tool_tokens += self._tokenizer.count(delivered)
                run.tool_history.append(f"{block.name}({block.input})")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": delivered,
                    }
                )
            messages.append({"role": "user", "content": results})

        run.error = "max turns exceeded"
        run.final_text = "CANNOT FIND"
        return run


def _tools_as_openai_functions() -> list[dict[str, Any]]:
    """Translate the benchmark tool definitions to OpenAI function format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]


class OpenAICompatAgent:
    """Tool-use loop over any OpenAI-compatible chat-completions API.

    Works with OpenAI, Google Gemini, Groq, Mistral, OpenRouter,
    DeepSeek, xAI, Azure OpenAI, and local Ollama/vLLM servers — any
    endpoint speaking the chat-completions protocol with tool calling.

    Example:
        >>> agent = OpenAICompatAgent("gpt-4o-mini")                    # OpenAI
        >>> agent = OpenAICompatAgent(
        ...     "llama3.1", base_url="http://localhost:11434/v1")       # Ollama
        >>> agent = OpenAICompatAgent(
        ...     "gemini-2.0-flash",
        ...     base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ...     api_key=os.environ["GEMINI_API_KEY"])                   # Gemini
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        client: Any = None,
    ) -> None:
        """Initialize the agent.

        Args:
            model: Provider model identifier.
            base_url: OpenAI-compatible endpoint; None uses OpenAI itself.
            api_key: Provider API key; local servers accept a dummy value.
            client: Injected client (tests); overrides base_url/api_key.
        """
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=base_url,
                # Local servers (Ollama/vLLM) require no key, but the
                # SDK insists on a non-empty value.
                api_key=api_key or "not-needed",
            )
        self.model = model
        self.client = client
        self._tokenizer = TokenCounter.get_default()

    def run(self, question: str, transform: Transform) -> AgentRun:
        """Run the loop until the model answers or MAX_TURNS is hit."""
        import json as json_mod

        run = AgentRun()
        tools = _tools_as_openai_functions()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        for _ in range(MAX_TURNS):
            run.turns += 1
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                tools=tools,
                messages=messages,
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                run.api_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                run.api_output_tokens += getattr(usage, "completion_tokens", 0) or 0

            message = response.choices[0].message
            tool_calls = message.tool_calls or []

            if not tool_calls:
                run.final_text = message.content or ""
                return run

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                try:
                    args = json_mod.loads(tc.function.arguments or "{}")
                except json_mod.JSONDecodeError:
                    args = {}
                raw = execute_tool(tc.function.name, args)
                delivered = transform(tc.function.name, raw, args)
                run.tool_calls += 1
                run.raw_tool_tokens += self._tokenizer.count(raw)
                run.delivered_tool_tokens += self._tokenizer.count(delivered)
                run.tool_history.append(f"{tc.function.name}({args})")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": delivered,
                    }
                )

        run.error = "max turns exceeded"
        run.final_text = "CANNOT FIND"
        return run


class ScriptedAgent:
    """Deterministic agent: reads the target file, answers from evidence.

    The task must provide target_file and answer_regex. The agent
    "answers" by scanning the delivered tool result for lines matching
    the expected evidence — if compression pruned that content, the
    agent genuinely cannot answer, mirroring a real information-loss
    failure.
    """

    def __init__(self, model: str = "scripted") -> None:
        self.model = model
        self._tokenizer = TokenCounter.get_default()

    def run(
        self, question: str, transform: Transform, task: dict | None = None
    ) -> AgentRun:
        run = AgentRun()
        task = task or {}
        target = task.get("target_file", "")
        pattern = task.get("answer_regex", "")

        raw = execute_tool("read_file", {"path": target})
        delivered = transform("read_file", raw, {"path": target})
        run.tool_calls = 1
        run.turns = 1
        run.raw_tool_tokens = self._tokenizer.count(raw)
        run.delivered_tool_tokens = self._tokenizer.count(delivered)
        run.tool_history.append(f"read_file({target})")

        match = re.search(pattern, delivered, re.IGNORECASE)
        if match:
            # Answer with the evidence line, like an extractive reader.
            line_start = delivered.rfind("\n", 0, match.start()) + 1
            line_end = delivered.find("\n", match.end())
            line_end = line_end if line_end != -1 else len(delivered)
            run.final_text = delivered[line_start:line_end].strip()
        else:
            run.final_text = "CANNOT FIND"
        return run
