"""Compression endpoint used by the SDKs and the MCP proxy fallback."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from contextmesh.core.chunker.base import CompressionInput
from contextmesh.dashboard.backend.state import get_state

router = APIRouter()


class CompressRequest(BaseModel):
    """Request body for compression endpoint."""

    session_id: str
    task_id: str
    tool_name: str
    tool_args: dict[str, Any] = {}
    raw_output: str
    task_description: str
    recent_steps: list[str] = []
    budget_tokens: int = 0


@router.post("/api/compress")
async def compress(request: CompressRequest) -> dict[str, Any]:
    """Compress tool output with the shared pipeline."""
    import anyio.to_thread

    state = get_state()

    budget = request.budget_tokens or state.config.budget_for_tool(request.tool_name)

    inp = CompressionInput(
        session_id=request.session_id,
        task_id=request.task_id,
        tool_name=request.tool_name,
        tool_args=request.tool_args,
        raw_output=request.raw_output,
        task_description=request.task_description,
        recent_steps=request.recent_steps,
        budget_tokens=budget,
    )

    # The pipeline is CPU-bound and synchronous; keep the event loop
    # free so health/stat endpoints stay responsive during compression.
    result = await anyio.to_thread.run_sync(state.pipeline.compress, inp)

    return {
        "compressed_output": result.compressed_output,
        "original_tokens": result.original_tokens,
        "compressed_tokens": result.compressed_tokens,
        "compression_ratio": result.compression_ratio,
        "chunks_selected": result.chunks_selected,
        "chunks_total": result.chunks_total,
        "trace_id": result.trace_id,
        "chunk_types_selected": result.chunk_types_selected,
    }
