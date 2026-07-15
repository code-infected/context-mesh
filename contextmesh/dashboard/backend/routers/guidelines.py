"""ACON guideline endpoints: current multipliers and audit history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from contextmesh.dashboard.backend.state import get_state

router = APIRouter()


@router.get("/api/guidelines")
async def get_guidelines() -> dict[str, list[dict[str, Any]]]:
    """Current extraction guidelines."""
    return {"guidelines": get_state().guideline_store.to_records()}


@router.get("/api/guidelines/history")
async def get_guideline_history() -> dict[str, list[dict[str, Any]]]:
    """Guideline update history, newest first."""
    return {"history": get_state().guideline_engine.get_history()}
