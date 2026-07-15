"""Session endpoints: per-session stats and traces."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from contextmesh.dashboard.backend.state import get_state

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions() -> dict[str, list[dict[str, Any]]]:
    """List sessions with compression stats."""
    return {"sessions": get_state().trace_store.session_summaries()}


@router.get("/api/sessions/{session_id}/traces")
async def get_session_traces(session_id: str) -> dict[str, list[dict[str, Any]]]:
    """All compression traces for a session."""
    traces = get_state().trace_store.get_traces_for_session(session_id)
    return {"traces": [t.to_dict() for t in traces]}


@router.get("/api/traces/{trace_id}/diff")
async def get_trace_diff(trace_id: str) -> dict[str, Any]:
    """Chunk-level diff for one compression: kept vs pruned, with previews."""
    diff = get_state().get_trace_diff(trace_id)
    if diff is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Trace not found or has no stored chunk previews "
                "(older trace, or the call produced too many chunks)"
            ),
        )
    return diff
