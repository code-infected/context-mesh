"""Tests for the benchmark agent implementations (no network).

The OpenAI-compatible loop is exercised with a scripted fake client;
provider resolution covers the --provider auto logic.
"""

from types import SimpleNamespace

from contextmesh.benchmarks.real_agent.agent import OpenAICompatAgent
from contextmesh.benchmarks.real_agent.runner import resolve_provider


def _tool_call(call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _response(message, prompt_tokens=100, completion_tokens=20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


class FakeOpenAIClient:
    """Scripted chat-completions client: one tool call, then an answer."""

    def __init__(self):
        self.requests = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                if len(outer.requests) == 1:
                    return _response(
                        SimpleNamespace(
                            content=None,
                            tool_calls=[
                                _tool_call(
                                    "call_1", "read_file",
                                    '{"path": "architecture.md"}',
                                )
                            ],
                        )
                    )
                # Second turn: answer from the delivered tool result.
                tool_msg = next(
                    m for m in kwargs["messages"] if m["role"] == "tool"
                )
                found = "PostgreSQL 16" in tool_msg["content"]
                return _response(
                    SimpleNamespace(
                        content="PostgreSQL 16 with pgvector" if found else "CANNOT FIND",
                        tool_calls=None,
                    )
                )

        self.chat = SimpleNamespace(completions=_Completions())


class TestOpenAICompatAgent:
    def test_tool_loop_round_trip(self) -> None:
        client = FakeOpenAIClient()
        agent = OpenAICompatAgent("any-model", client=client)

        run = agent.run(
            "Which database does architecture.md name?",
            transform=lambda tool, raw, args: raw,
        )

        assert run.tool_calls == 1
        assert run.turns == 2
        assert "PostgreSQL 16" in run.final_text
        assert run.raw_tool_tokens > 0
        assert run.delivered_tool_tokens == run.raw_tool_tokens
        assert run.api_input_tokens == 200 and run.api_output_tokens == 40

        # Tool schema translated to OpenAI function format.
        tools = client.requests[0]["tools"]
        assert all(t["type"] == "function" for t in tools)
        assert {t["function"]["name"] for t in tools} == {"list_files", "read_file"}

        # Assistant tool_calls echoed back, tool result linked by id.
        second = client.requests[1]["messages"]
        assert any(
            m["role"] == "assistant" and m.get("tool_calls") for m in second
        )
        assert any(
            m["role"] == "tool" and m["tool_call_id"] == "call_1" for m in second
        )

    def test_compression_transform_is_applied(self) -> None:
        client = FakeOpenAIClient()
        agent = OpenAICompatAgent("any-model", client=client)

        run = agent.run(
            "Which database does architecture.md name?",
            transform=lambda tool, raw, args: "compressed away entirely",
        )

        # Evidence was pruned by the transform -> model cannot find it.
        assert run.final_text == "CANNOT FIND"
        assert run.delivered_tool_tokens < run.raw_tool_tokens


class TestProviderResolution:
    def test_auto_routing(self) -> None:
        assert resolve_provider("auto", "scripted") == "scripted"
        assert resolve_provider("auto", "claude-sonnet-5") == "anthropic"
        assert resolve_provider("auto", "gpt-4o-mini") == "openai"
        assert resolve_provider("auto", "llama3.1") == "openai"
        assert resolve_provider("auto", "gemini-2.0-flash") == "openai"

    def test_explicit_provider_wins(self) -> None:
        assert resolve_provider("openai", "claude-sonnet-5") == "openai"
        assert resolve_provider("anthropic", "anything") == "anthropic"
