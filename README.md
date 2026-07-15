# ContextMesh

[![CI](https://github.com/code-infected/context-mesh/actions/workflows/ci.yml/badge.svg)](https://github.com/code-infected/context-mesh/actions/workflows/ci.yml)

> A production, MCP-native context compression runtime for long-running AI agents.
> Intercepts tool outputs before they enter the agent context window, extracts only
> task-relevant content, and improves extraction quality over time through failure analysis.

## Overview

ContextMesh sits between an AI agent's reasoning loop and the tools it calls. When a tool
returns output (e.g., a 40k-token file read), ContextMesh compresses it to only the
task-relevant sections before the agent sees it. It is model-agnostic: scoring uses a
local embedding model, so no LLM API is involved in the hot path.

**Key capabilities:**
- **Type-aware chunking**: AST-based for code (Python/TS/JS/Rust/Go via tree-sitter),
  structure-preserving for JSON, heading-based for Markdown, plus HTML, CSV, logs, and shell
- **Task-conditioned relevance scoring**: semantic embeddings blended with an exact-token
  lexical signal; rolling task context that follows the agent's recent steps
- **Budget-constrained extraction**: greedy knapsack selection with dependency awareness
  and coherence validation (signatures travel with bodies, JSON stays valid)
- **Fail-open by construction**: timeouts, vague tasks, tiny outputs, and errors all
  return the raw output — compression never blocks or truncates a tool call to nothing
- **ACON failure loop**: failed tasks whose errors implicate pruned context update
  per-(tool, chunk-type) scoring guidelines, persisted to PostgreSQL, decaying over time
- **Transparent MCP proxy**: wraps one or many upstream MCP servers (stdio / Streamable
  HTTP / SSE), passes through resources and prompts, adaptive per-session budgets
- **Observability dashboard**: sessions, per-tool stats, kept-vs-pruned chunk diffs,
  guideline history, and an interactive compression playground
- **Result caching + inter-call dedup**: identical calls answer in milliseconds; optional
  mode never re-sends chunks a session has already received

**Target metrics:**
- 75%+ token reduction per tool call
- <5 percentage point task completion degradation
- <80ms P99 latency overhead (warm caches)

## Quick Start

```bash
# Install (add ".[embeddings]" for semantic scoring — pulls in torch;
# without it, a fast lexical fallback scorer is used)
pip install -e ".[dev]"

# Run the compression pipeline directly
python -m contextmesh.core \
  --tool-name read_file \
  --input tests/fixtures/large_python_file.py \
  --task "find all authentication-related functions" \
  --budget 1200 \
  --json

# Run the test suite
pytest tests
```

### Run the full stack locally

```bash
# 1. gRPC compression service (used by the MCP proxy)
python grpc_server.py --port 50051

# 2. Dashboard API (also serves POST /api/compress for the SDKs)
uvicorn contextmesh.dashboard.backend.main:app --port 8082

# 3. Dashboard frontend — either dev server (http://localhost:3000)...
cd contextmesh/dashboard/frontend && npm install && npm run dev
# ...or build once and let the backend serve it at http://127.0.0.1:8082/
cd contextmesh/dashboard/frontend && npm run build

# 4. MCP proxy wrapping a filesystem tool server (stdio)
cd contextmesh/proxy/mcp_proxy && npm install && npm run build
CONTEXTMESH_UPSTREAM_COMMAND="npx -y @modelcontextprotocol/server-filesystem /repo" \
  node dist/index.js
```

Traces persist in memory by default. To use PostgreSQL, set
`CONTEXTMESH_DATABASE_URL` (or `database.url` in `config.yaml`) and run
`python -m contextmesh.db migrate`. `docker-compose up` brings up
postgres + migration + gRPC service + dashboard together.

### Use with Claude Code (no API key needed)

Claude Code is an MCP client, so it can consume tools through the
ContextMesh proxy directly — compression runs locally and Claude Code
uses your existing login. Drop a `.mcp.json` into your project:

```json
{
  "mcpServers": {
    "contextmesh": {
      "command": "node",
      "args": ["<repo>/contextmesh/proxy/mcp_proxy/dist/index.js"],
      "env": {
        "CONTEXTMESH_UPSTREAM_COMMAND": "npx -y @modelcontextprotocol/server-filesystem <your-project-dir>",
        "CONTEXTMESH_GRPC_HOST": "localhost",
        "CONTEXTMESH_GRPC_PORT": "50051",
        "CONTEXTMESH_DEFAULT_BUDGET_TOKENS": "2000"
      }
    }
  }
}
```

Keep `python grpc_server.py` running, restart Claude Code in that
directory, approve the server, and file reads made through the
contextmesh tools come back compressed (check `_meta.contextmesh` for
the ratio). The same pattern works for any MCP-compatible agent.
Multi-upstream and Streamable HTTP modes are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### SDK usage (custom agent loops)

```python
from contextmesh import ContextMesh

cm = ContextMesh(task_description="refactor auth to use JWT", budget_tokens=8000)
compressed = cm.compress(output=raw, tool_name="read_file",
                         tool_args={"path": "/src/auth.py"})
# ... task finishes ...
cm.report_outcome(compressed.task_id, success=False,
                  failure_reason="ImportError: cannot import verify_token")
# failures matching compression patterns update extraction guidelines (ACON)
```

### Benchmarks

ContextMesh itself is model-agnostic (local embedding scorer, no LLM
calls in the hot path). The benchmark harness drives Claude natively
or any OpenAI-compatible provider:

```bash
# Information-preservation harness (deterministic, no API key needed):
python -m contextmesh.benchmarks.real_agent.runner --model scripted

# Claude (ANTHROPIC_API_KEY):
python -m contextmesh.benchmarks.real_agent.runner --model claude-sonnet-5

# OpenAI (OPENAI_API_KEY):
python -m contextmesh.benchmarks.real_agent.runner --model gpt-4o-mini

# Local Ollama — no API key:
python -m contextmesh.benchmarks.real_agent.runner \
  --model llama3.1 --base-url http://localhost:11434/v1

# Gemini / Groq / Mistral / OpenRouter / DeepSeek / xAI via their
# OpenAI-compatible endpoints:
python -m contextmesh.benchmarks.real_agent.runner \
  --model gemini-2.0-flash \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \
  --api-key-env GEMINI_API_KEY
```

Scripted-harness result on the bundled corpus (code, JSON, logs,
markdown; budget 1500; bge-small-en-v1.5 scorer):

| condition   | tasks | success | tool tokens | reduction |
|-------------|------:|--------:|------------:|----------:|
| baseline    | 8     | 100%    | 87,970      | 0%        |
| contextmesh | 8     | 100%    | 9,104       | **89.7%** |

Every task's needle (an exact string buried in a large tool output)
survived compression. The scripted harness verifies information
preservation, not model behavior — run with a real model for
completion-rate claims.

## Architecture

```
Agent -> MCP Proxy -> Upstream Tool Server(s) -> raw output
  -> Type Detector -> Type-Aware Chunker -> Dependency Analyzer
  -> Relevance Scorer -> Budget-Constrained Extractor -> Coherence Validator
  -> compressed output -> agent
  -> Trace Recorder -> ACON Failure Loop -> guideline updates
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Components, endpoints, ports, configuration, and performance notes
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development guidelines

## Research Foundation

ContextMesh implements two research directions, as cited in the
original project specification:

1. **Squeez** (arXiv:2604.04979, 2026): Task-conditioned extractive pruning for mixed-format tool output
2. **ACON** (arXiv:2510.00615, 2025): Failure-driven guideline optimization for context compression

## License

MIT
