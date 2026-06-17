"""FastAPI backend for ContextMesh dashboard.

Provides REST API endpoints for:
- Session management and tracing
- Compression statistics per tool
- ACON guideline history
- Failure analysis
"""

from __future__ import annotations

from contextmesh.core.chunker.base import CompressionInput, CompressionOutput
