"""Failure endpoints: analyzed failures and outcome ingestion."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from contextmesh.dashboard.backend.state import get_state
from contextmesh.feedback.failure_detector import TaskOutcome, TaskOutcomeEvent

router = APIRouter()


class TaskOutcomeRequest(BaseModel):
    """Request body for task outcome reporting."""

    task_id: str
    session_id: str | None = None
    outcome: str
    failure_reason: str | None = None
    agent_final_output: str | None = None
    evaluation_score: float | None = None


@router.get("/api/failures")
async def get_failures() -> dict[str, list[dict[str, Any]]]:
    """Tasks flagged by the failure detector, newest first."""
    return {"failures": get_state().all_failures()}


@router.post("/api/tasks/{task_id}/outcome")
async def report_task_outcome(
    task_id: str,
    request: TaskOutcomeRequest,
) -> dict[str, Any]:
    """Report a task outcome; failed tasks trigger ACON analysis."""
    try:
        outcome = TaskOutcome(request.outcome)
    except ValueError:
        outcome = TaskOutcome.UNKNOWN

    event = TaskOutcomeEvent(
        task_id=task_id,
        session_id=request.session_id,
        outcome=outcome,
        failure_reason=request.failure_reason,
        evaluation_score=request.evaluation_score,
        agent_final_output=request.agent_final_output,
    )
    record = get_state().record_outcome(event)

    response: dict[str, Any] = {"status": "ok", "outcome": outcome.value}
    if record is not None:
        response["compression_implicated"] = record.compression_implicated
        response["traces_analyzed"] = len(record.trace_ids)
    return response
