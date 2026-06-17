# Contributing to ContextMesh

## Development Setup

```bash
# Clone the repository
git clone https://github.com/code-infected/context-mesh.git
cd context-mesh

# Install dependencies
pip install -e ".[dev]"

# Install additional dependencies for specific components
pip install -e ".[embeddings]"   # For scorer development
pip install -e ".[dashboard]"    # For dashboard development

# Run tests
pytest

# Run linting
ruff check .
mypy contextmesh/
```

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
pytest tests/core/test_chunker.py

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
```
