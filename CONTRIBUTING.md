# Contributing to ContextMesh

## Development Setup

```bash
# Clone the repository
git clone https://github.com/code-infected/context-mesh.git
cd context-mesh

# Install dependencies
pip install -e ".[dev]"

# Install additional dependencies for specific components
pip install -e ".[embeddings]"   # Semantic scoring (torch); tests pass without it too
pip install -e ".[dashboard]"    # FastAPI backend
pip install -e ".[benchmarks]"   # anthropic + openai for the real-agent benchmark

# Run tests (dashboard tests also need: pip install httpx)
pytest

# Run linting — same invocation CI uses
ruff check contextmesh grpc_server.py tests
```

TypeScript components build separately:

```bash
cd contextmesh/proxy/mcp_proxy && npm install && npm run build
cd contextmesh/proxy/sdk/typescript && npm install && npm run build
cd contextmesh/dashboard/frontend && npm install && npm run build
```

CI (`.github/workflows/ci.yml`) runs ruff, the Python test suite
against a pgvector PostgreSQL service (including a live schema
migration), and all three npm builds. All of it must pass.

## Branching Strategy

- `main`: Production-ready code
- `feature/*`: New features
- `fix/*`: Bug fixes
- `refactor/*`: Code refactoring
- `docs/*`: Documentation updates

## Commit Messages

Follow Conventional Commits format:

```
type(scope): description

feat(core): add tree-sitter AST chunker for Python
fix(scorer): handle empty embeddings in cache
refactor(extractor): simplify dependency resolution
docs(readme): update quick start instructions
test(chunker): add pytest fixtures for JSON test cases
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

## Code Standards

- Type hints required on all public functions
- Docstrings on all public classes and functions
- 100 character line limit
- Unit tests for all public functions in `tests/`
- Integration tests for multi-component flows

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=contextmesh --cov-report=html

# Run specific test file
pytest tests/test_chunkers.py

# Run with verbose output
pytest -v
```

## Component Dependencies

When modifying components, be aware of dependencies:

```
core/chunker/*      <- base.py (foundational)
core/scorer/*       <- chunker output, guideline_adjuster
core/extractor/*    <- scorer output, chunker
core/validator/*    <- extractor output
core/pipeline.py    <- all core components
config.py           <- consumed by pipeline factory, gRPC server, dashboard, SDK
feedback/*          <- consumes traces from pipeline; feeds guideline_adjuster
dashboard/backend   <- pipeline + feedback (shared guideline store)
proxy/mcp_proxy     <- gRPC service (grpc_server.py); proto in proxy/mcp_proxy/proto
```

The gRPC stubs in `contextmesh/grpc/compression_pb2*.py` are generated
from `contextmesh/proxy/mcp_proxy/proto/compression.proto` and are
committed. If you change the proto, regenerate them with:

```bash
python -m grpc_tools.protoc -I contextmesh/proxy/mcp_proxy/proto \
  --python_out=contextmesh/grpc --grpc_python_out=contextmesh/grpc \
  --pyi_out=contextmesh/grpc compression.proto
```
