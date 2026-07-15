# ContextMesh Architecture

ContextMesh is middleware in the agent-tool communication path. Every
tool response passes through it before the agent sees it; only the
task-relevant portion (under a token budget) comes out the other side.

```
Agent -> MCP Proxy / SDK -> Upstream Tool Server(s) -> raw output
  -> Type Detector -> Type-Aware Chunker -> Dependency Analyzer
  -> Relevance Scorer -> Budget-Constrained Extractor -> Coherence Validator
  -> compressed output -> agent
  -> Trace Recorder (async) -> ACON Failure Loop -> guideline updates
```

## Components

### Core pipeline (`contextmesh/core/`) — Python, no LLM calls

| Component | Files | What it does |
|---|---|---|
| Chunkers | `chunker/{code,json,log,html,csv,shell,markdown,mixed}_chunker.py` | Format-specific segmentation into coherent units. Code uses tree-sitter ASTs (Python/TS/TSX/JS/Rust/Go) with call-graph dependencies and oversized function/class splitting; JSON emits non-overlapping, individually-valid JSON subtrees; markdown splits at headings (fence-aware). |
| Scorer | `scorer/embed_scorer.py`, `scorer/cache.py` | Embeds task context and chunk heads in one space (`BAAI/bge-small-en-v1.5`), blends cosine similarity with a hashed-BoW lexical score for exact identifiers. Rolling task context: 0.4 × task + 0.6 × recent steps. Falls back to pure lexical scoring when sentence-transformers isn't installed. LRU-cached embeddings. |
| Guideline adjuster | `scorer/guideline_adjuster.py` | Applies ACON-learned (tool, chunk_type) score multipliers; refreshes from PostgreSQL every 60s when configured. |
| Extractor | `extractor/budget_extractor.py` | Greedy budget-constrained selection with transitive dependency resolution (knapsack variant); dependency slack keeps signatures with bodies. |
| Validator | `validator/coherence_checker.py` | Force-includes missing dependencies of selected code chunks, drops unparseable JSON chunks. |
| Pipeline | `pipeline.py` | Orchestrates everything. Thread-safe; hard-deadline abort with fail-open; result cache for identical calls; optional inter-call session dedup; chunk-count cap + head/tail pre-filter for giant outputs; oversized-chunk splitting; per-chunk previews/scores recorded into traces. |
| Config | `../config.py` | `config.yaml` + `CONTEXTMESH_<SECTION>_<KEY>` env overrides; `create_pipeline()` factory. |

Fail-open is a hard invariant: small outputs, vague task context
(uniform scores), insufficient savings, deadline overruns, empty
selections, and internal errors all return the raw output, never an
error and never an empty string.

### Feedback / ACON loop (`contextmesh/feedback/`)

- `trace_store.py` — every compression event (chunks kept/pruned,
  token counts, previews, low-signal flag) goes to PostgreSQL when
  `database.url` is set, else a bounded in-memory store. Fail-open;
  a dead database disables itself after repeated failures.
- `failure_detector.py` — classifies reported task failures whose
  error patterns implicate pruned context (ImportError, NameError, ...).
- `guideline_engine.py` — the ACON loop: a failed task counts one
  failure per (tool, chunk_type) it pruned; after
  `min_failures_before_update` (default 3) the multiplier is bumped
  ×1.2 (capped at 3.0), decaying back toward 1.0 after
  `multiplier_decay_days` without new failures. Supports a pluggable
  full-context re-run callback for paired-trajectory root-cause diffs.
- `guideline_persistence.py` + `schema.sql` — guidelines, history,
  outcomes, and failures persist across restarts;
  `python -m contextmesh.db migrate` applies the schema.

### MCP proxy (`contextmesh/proxy/mcp_proxy/`) — TypeScript

Wraps one or more upstream MCP servers transparently: tools are
re-exposed 1:1 (name-collision prefixing across upstreams), responses
above ~1k tokens are compressed via gRPC to the Python service,
resources and prompts pass through. Serves stdio (default) or
Streamable HTTP (`CONTEXTMESH_PROXY_HTTP_PORT`) with concurrent
sessions. Upstreams may be stdio commands, Streamable HTTP, or SSE
URLs. Adaptive budgets shrink per-call budgets as a session approaches
`CONTEXTMESH_SESSION_TOKEN_LIMIT`. Agents can steer relevance via the
`contextmesh_set_task` tool.

### SDKs (`contextmesh/proxy/sdk/`)

- Python: `from contextmesh import ContextMesh` — in-process pipeline
  with tracing and a local ACON loop; `report_outcome()` feeds it.
- TypeScript: `@contextmesh/sdk` — HTTP client for the dashboard's
  `POST /api/compress`; fail-open.

### Dashboard (`contextmesh/dashboard/`)

FastAPI backend + React (Vite/Tailwind/Recharts) frontend. Serves the
built frontend at `/` when `frontend/dist` exists. Views: session
overview (KPIs, low-signal filter), per-tool stats, failure analysis
with kept-vs-pruned chunk diffs, ACON guideline history, and an
interactive compression playground. Optional bearer auth via
`CONTEXTMESH_DASHBOARD_API_TOKEN`.

Key endpoints: `/api/compress`, `/api/sessions[/{id}/traces]`,
`/api/traces/{id}/diff`, `/api/tools/stats`, `/api/guidelines[/history]`,
`/api/failures`, `/api/tasks/{id}/outcome`, `/api/stats/overview`,
`/api/health`.

### Benchmarks (`contextmesh/benchmarks/real_agent/`)

A tool-use agent loop over a verifiable corpus, run baseline vs.
compressed. Drivers: native Anthropic, any OpenAI-compatible endpoint
(OpenAI, Gemini, Groq, Mistral, OpenRouter, DeepSeek, xAI, local
Ollama/vLLM), or a deterministic scripted agent that requires no API
key and fails a task whenever compression prunes the needed evidence.

## Ports and processes

| Process | Port | Command |
|---|---|---|
| gRPC compression service | 50051 | `python grpc_server.py` |
| Dashboard API (+ built frontend) | 8082 | `uvicorn contextmesh.dashboard.backend.main:app --port 8082` |
| Frontend dev server (optional) | 3000 | `npm run dev` in `dashboard/frontend` |
| MCP proxy | stdio / `CONTEXTMESH_PROXY_HTTP_PORT` | `node dist/index.js` in `proxy/mcp_proxy` |

On Windows, bind is IPv4 — use `127.0.0.1`, not `localhost`.

## Configuration

`config.yaml` at the working directory (or `CONTEXTMESH_CONFIG`) with
env overrides of the form `CONTEXTMESH_<SECTION>_<KEY>`, e.g.
`CONTEXTMESH_DATABASE_URL`, `CONTEXTMESH_COMPRESSION_DEFAULT_BUDGET_TOKENS`,
`CONTEXTMESH_CHUNKER_CODE_MAX_CHUNK_TOKENS`. See the annotated
`config.yaml` for every key. Notable behavior knobs:

- `compression.hard_timeout_ms` — hard fail-open deadline (default
  10s; cold-cache CPU embedding takes seconds, warm calls take tens of
  milliseconds).
- `compression.session_dedup` — never re-send chunks already delivered
  to a session (off by default).
- `database.url` — empty means in-memory traces/guidelines; set it for
  persistence.

## Performance expectations

The 80ms soft latency target assumes warm caches. Cold start pays a
one-time model load (seconds) plus first-encode cost per unique chunk;
identical repeated calls are served from the result cache in
single-digit milliseconds. Scoring embeds only chunk heads (~400
chars) and caches per chunk-content hash.
