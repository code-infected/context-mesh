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
# Install dependencies
pip install -e ".[dev]"

# Start PostgreSQL with pgvector
docker-compose up -d postgres

# Run the compression pipeline directly
python -m contextmesh.pipeline \
  --tool-name read_file \
  --input tests/fixtures/large_python_file.py \
  --task "find all authentication-related functions" \
  --budget 4000
```

## Architecture

```
Agent -> MCP Proxy -> Upstream Tool Server -> raw output
  -> Type Detector -> Type-Aware Chunker -> Dependency Analyzer
  -> Relevance Scorer -> Budget-Constrained Extractor -> Coherence Validator
  -> compressed output -> agent
```

## Documentation

- [Project Specification](.opencode/contextmesh.md) - Full technical specification
- [Knowledge Graph](.opencode/KNOWLEDGE_GRAPH.md) - Component relationships and dependencies
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development guidelines

## Research Foundation

ContextMesh implements two research contributions:

1. **Squeez** (arXiv:2604.04979, 2026): Task-conditioned extractive pruning for mixed-format tool output
2. **ACON** (arXiv:2510.00615, 2025): Failure-driven guideline optimization for context compression

## License

MIT
