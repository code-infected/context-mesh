"""Integration tests for the dashboard API (in-memory trace store)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE = Path(__file__).parent / "fixtures" / "large_python_file.py"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # Force the in-memory backend regardless of local config, and give
    # compression a generous deadline (functional tests, slow CI CPUs).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONTEXTMESH_DATABASE_URL", raising=False)
    monkeypatch.setenv("CONTEXTMESH_COMPRESSION_HARD_TIMEOUT_MS", "120000")

    from contextmesh.dashboard.backend import state
    from contextmesh.dashboard.backend.main import app

    state.reset_state()
    with TestClient(app) as test_client:
        yield test_client
    state.reset_state()


def compress_and_fail(client: TestClient, task_id: str) -> dict:
    r = client.post("/api/compress", json={
        "session_id": "sess-1",
        "task_id": task_id,
        "tool_name": "read_file",
        "tool_args": {"path": "/src/auth.py"},
        "raw_output": FIXTURE.read_text(encoding="utf-8"),
        "task_description": "find all authentication-related functions",
        "budget_tokens": 1200,
    })
    assert r.status_code == 200
    body = r.json()

    r = client.post(f"/api/tasks/{task_id}/outcome", json={
        "task_id": task_id,
        "session_id": "sess-1",
        "outcome": "failed",
        "failure_reason": "ImportError: cannot import name 'verify_token'",
    })
    assert r.status_code == 200
    return body


class TestDashboardAPI:
    def test_compress_endpoint(self, client) -> None:
        body = compress_and_fail(client, "task-0")
        assert body["compression_ratio"] < 0.5
        assert body["trace_id"]

    def test_full_feedback_cycle(self, client) -> None:
        for i in range(3):
            compress_and_fail(client, f"task-{i}")

        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["traces_stored"] == 3
        assert health["sessions"] == 1

        sessions = client.get("/api/sessions").json()["sessions"]
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["trace_count"] == 3
        assert sessions[0]["tokens_saved"] > 0

        traces = client.get("/api/sessions/sess-1/traces").json()["traces"]
        assert len(traces) == 3
        assert all(t["compression_ratio"] < 1.0 for t in traces)

        tools = client.get("/api/tools/stats").json()["tools"]
        assert tools[0]["tool_name"] == "read_file"
        assert tools[0]["call_count"] == 3
        assert tools[0]["failure_count"] == 3

        failures = client.get("/api/failures").json()["failures"]
        assert len(failures) == 3
        assert all(f["compression_implicated"] for f in failures)

        guidelines = client.get("/api/guidelines").json()["guidelines"]
        boosted = [g for g in guidelines if g["score_multiplier"] > 1.0]
        assert boosted, "three failures should trigger a guideline update"
        assert all(g["score_multiplier"] == 1.2 for g in boosted)

        history = client.get("/api/guidelines/history").json()["history"]
        assert history

    def test_trace_diff_endpoint(self, client) -> None:
        body = compress_and_fail(client, "task-diff")
        trace_id = body["trace_id"]

        r = client.get(f"/api/traces/{trace_id}/diff")
        assert r.status_code == 200
        diff = r.json()
        assert diff["trace_id"] == trace_id
        assert diff["tool_name"] == "read_file"
        assert diff["chunks"], "diff should list chunks"

        kept = [c for c in diff["chunks"] if c["selected"]]
        pruned = [c for c in diff["chunks"] if not c["selected"]]
        assert kept and pruned
        assert all(c["preview"] for c in diff["chunks"])
        assert any(c["score"] is not None for c in diff["chunks"])

        r = client.get("/api/traces/does-not-exist/diff")
        assert r.status_code == 404
        assert "detail" in r.json()

    def test_cache_hit_records_new_trace(self, client) -> None:
        """Identical calls served from cache still record per-task traces."""
        compress_and_fail(client, "task-a")
        compress_and_fail(client, "task-b")  # identical content -> cache hit

        traces = client.get("/api/sessions/sess-1/traces").json()["traces"]
        task_ids = {t["task_id"] for t in traces}
        assert {"task-a", "task-b"} <= task_ids

    def test_bearer_auth_when_token_set(self, client, monkeypatch) -> None:
        monkeypatch.setenv("CONTEXTMESH_DASHBOARD_API_TOKEN", "secret-token")

        r = client.get("/api/sessions")
        assert r.status_code == 401

        r = client.get(
            "/api/sessions", headers={"Authorization": "Bearer secret-token"}
        )
        assert r.status_code == 200

        # Health stays open for probes.
        assert client.get("/api/health").status_code == 200

    def test_stats_overview(self, client) -> None:
        for i in range(2):
            compress_and_fail(client, f"task-ov-{i}")

        r = client.get("/api/stats/overview")
        assert r.status_code == 200
        overview = r.json()
        assert overview["sessions"] == 1
        assert overview["traces"] == 2
        assert overview["tokens_saved"] > 0
        assert overview["failures"] == 2
        assert overview["compression_implicated_failures"] == 2
        assert 0 < overview["avg_compression_ratio"] < 1
        assert overview["trace_backend"] == "memory"
        assert isinstance(overview["scorer_fallback"], bool)

    def test_json_tool_output(self, client) -> None:
        data = {"rows": [{"id": i, "text": "lorem " * 40} for i in range(50)]}
        r = client.post("/api/compress", json={
            "session_id": "sess-2",
            "task_id": "task-json",
            "tool_name": "query_database",
            "tool_args": {"q": "select *"},
            "raw_output": json.dumps(data),
            "task_description": "find row 7 text",
            "budget_tokens": 1000,
        })
        assert r.status_code == 200
        body = r.json()
        if body["compression_ratio"] < 1.0:
            json.loads(body["compressed_output"])
