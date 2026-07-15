# ContextMesh

> A production, MCP-native context compression runtime for long-running AI agents.
> Intercepts tool outputs before they enter the agent context window, extracts only
> task-relevant content, and improves extraction quality over time through failure analysis.

## Overview

ContextMesh sits between an AI agent's reasoning loop and the tools it calls. When a tool
returns output (e.g., a 40k-token file read), ContextMesh compresses it to only the
task-relevant sections before the agent sees it.

**Key capabilities:**
- **Type-aware chunking**: Format-specific segmentation (AST-based for code, key-depth for JSON, DOM-based for HTML, etc.)
- **Task-conditioned relevance scoring**: Embedding-based scoring against task context
- **Budget-constrained extraction**: 0-1 knapsack selection with dependency awareness
- **Coherence validation**: Ensures extracted chunks form readable output
- **ACON failure loop**: Offline feedback that learns from compression failures

**Target metrics:**
- 75%+ token reduction per tool call
- <5 percentage point task completion degradation
- <80ms P99 latency overhead

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

- [Knowledge Graph](.opencode/KNOWLEDGE_GRAPH.md) - Component relationships, implementation state, and known limitations
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development guidelines

## Research Foundation

ContextMesh implements two research contributions:

1. **Squeez** (arXiv:2604.04979, 2026): Task-conditioned extractive pruning for mixed-format tool output
2. **ACON** (arXiv:2510.00615, 2025): Failure-driven guideline optimization for context compression

## License

MIT
