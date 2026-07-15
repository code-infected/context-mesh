"""FastAPI backend for ContextMesh dashboard.

Provides REST API endpoints for:
- Session management and tracing
- Compression statistics per tool
- ACON guideline state and history
- Failure analysis
- Compression (used by SDKs and as the MCP proxy's HTTP fallback)

Run:
    uvicorn contextmesh.dashboard.backend.main:app --port 8082
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from contextmesh.dashboard.backend.routers import (
    compress,
    failures,
    guidelines,
    sessions,
    tools,
)
from contextmesh.dashboard.backend.state import get_state

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared state on startup, flush traces on shutdown."""
    state = get_state()
    logger.info(
        "ContextMesh dashboard backend starting (trace backend: %s)",
        state.trace_store.backend_name,
    )
    state.start_decay_scheduler()
    yield
    state.stop_decay_scheduler()
    state.trace_store.flush()
    logger.info("ContextMesh dashboard backend stopped")


app = FastAPI(
    title="ContextMesh Dashboard API",
    description="REST API for ContextMesh observability dashboard",
    version="0.1.0",
    lifespan=lifespan,
)

# The React dev server (vite, port 3000) calls the API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Optional bearer auth: set CONTEXTMESH_DASHBOARD_API_TOKEN to require
# `Authorization: Bearer <token>` on every API endpoint except health.
@app.middleware("http")
async def bearer_auth(request: Request, call_next: Any) -> Any:
    token = os.environ.get("CONTEXTMESH_DASHBOARD_API_TOKEN")
    if (
        token
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
    ):
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {token}"
        if not hmac.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def request_timing(request: Request, call_next: Any) -> Any:
    """Log API request timings (skips static assets)."""
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response

app.include_router(sessions.router)
app.include_router(tools.router)
app.include_router(guidelines.router)
app.include_router(failures.router)
app.include_router(compress.router)


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    """Health check with store stats."""
    state = get_state()
    stats = state.trace_store.get_stats()
    return {
        "status": "ok",
        "traces_stored": stats.get("trace_count", 0),
        "sessions": stats.get("sessions", 0),
        "trace_backend": state.trace_store.backend_name,
    }


@app.get("/api/stats/overview")
async def stats_overview() -> dict[str, Any]:
    """Aggregate dashboard KPIs in a single call."""
    return get_state().overview()


# Serve the built React dashboard when it exists (single-origin deploy:
# `npm run build` in dashboard/frontend, then only the backend runs).
# Mounted last so /api routes take precedence.
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
    logger.info("Serving dashboard frontend from %s", _FRONTEND_DIST)
