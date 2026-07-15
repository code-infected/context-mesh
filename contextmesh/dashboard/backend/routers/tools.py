"""Per-tool compression statistics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from contextmesh.dashboard.backend.state import get_state

router = APIRouter()


@router.get("/api/tools/stats")
async def get_tool_stats() -> dict[str, list[dict[str, Any]]]:
    """Per-tool compression ratio and failure counts."""
    state = get_state()
    stats = state.trace_store.tool_stats()
    failure_counts = state.failure_counts_by_tool()
    for entry in stats:
        entry["failure_count"] = failure_counts.get(entry["tool_name"], 0)
    return {"tools": stats}
