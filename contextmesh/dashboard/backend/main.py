"""FastAPI backend for ContextMesh dashboard.

Provides REST API endpoints for:
- Session management and tracing
- Compression statistics per tool
- ACON guideline history
- Failure analysis
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TaskOutcomeRequest(BaseModel):
    """Request body for task outcome reporting."""

    task_id: str
    session_id: str | None = None
    outcome: str
    failure_reason: str | None = None
    agent_final_output: str | None = None
    evaluation_score: float | None = None


class CompressRequest(BaseModel):
    """Request body for compression endpoint."""

    session_id: str
    task_id: str
    tool_name: str
    tool_args: dict[str, Any] = {}
    raw_output: str
    task_description: str
    recent_steps: list[str] = []
    budget_tokens: int = 8000


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    Initializes database connections on startup,
    closes them on shutdown.
    """
    logger.info("Starting ContextMesh dashboard backend")
    yield
    logger.info("Shutting down ContextMesh dashboard backend")


app = FastAPI(
    title="ContextMesh Dashboard API",
    description="REST API for ContextMesh observability dashboard",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/sessions")
async def list_sessions() -> dict[str, list[dict[str, Any]]]:
    """List sessions with compression stats.

    Returns:
        Dictionary with sessions list.
    """
    return {"sessions": []}


@app.get("/api/sessions/{session_id}/traces")
async def get_session_traces(session_id: str) -> dict[str, list[dict[str, Any]]]:
    """Get all compression traces for a session.

    Args:
        session_id: Session identifier.

    Returns:
        Dictionary with traces list.
    """
    return {"traces": []}


@app.get("/api/tools/stats")
async def get_tool_stats() -> dict[str, dict[str, float]]:
    """Get per-tool compression statistics.

    Returns:
        Dictionary of tool_name -> stats.
    """
    return {}


@app.get("/api/guidelines")
async def get_guidelines() -> dict[str, list[dict[str, Any]]]:
    """Get current extraction guidelines with history.

    Returns:
        Dictionary with guidelines list.
    """
    return {"guidelines": []}


@app.get("/api/failures")
async def get_failures() -> dict[str, list[dict[str, Any]]]:
    """Get tasks flagged by failure detector.

    Returns:
        Dictionary with failures list.
    """
    return {"failures": []}


@app.post("/api/tasks/{task_id}/outcome")
async def report_task_outcome(
    task_id: str,
    request: TaskOutcomeRequest,
) -> dict[str, str]:
    """Report task outcome for ACON feedback loop.

    Args:
        task_id: Task identifier.
        request: Task outcome data.

    Returns:
        Success message.
    """
    logger.info(f"Task outcome reported: {task_id} -> {request.outcome}")
    return {"status": "ok"}


@app.post("/api/compress")
async def compress(request: CompressRequest) -> dict[str, Any]:
    """Compress tool output.

    Args:
        request: Compression request data.

    Returns:
        Compression result.
    """
    from contextmesh.core.chunker.base import CompressionInput
    from contextmesh.core.pipeline import CompressionPipeline

    inp = CompressionInput(
        session_id=request.session_id,
        task_id=request.task_id,
        tool_name=request.tool_name,
        tool_args=request.tool_args,
        raw_output=request.raw_output,
        task_description=request.task_description,
        recent_steps=request.recent_steps,
        budget_tokens=request.budget_tokens,
    )

    pipeline = CompressionPipeline()
    result = pipeline.compress(inp)

    return {
        "compressed_output": result.compressed_output,
        "original_tokens": result.original_tokens,
        "compressed_tokens": result.compressed_tokens,
        "compression_ratio": result.compression_ratio,
        "chunks_selected": result.chunks_selected,
        "chunks_total": result.chunks_total,
        "chunk_types_selected": result.chunk_types_selected,
    }


@app.get("/api/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        Health status.
    """
    return {"status": "healthy"}
