-- PostgreSQL schema for ContextMesh feedback and trace storage.
--
-- Supports:
--   compression_traces:    every compression event, for ACON analysis
--   extraction_guidelines: ACON-learned score multipliers
--   guideline_history:     audit trail for guideline changes
--   task_outcomes:         agent-reported task outcomes
--   failed_tasks:          failure-analysis work queue
--
-- Session and task identifiers are agent-supplied opaque strings, so
-- they are stored as TEXT (not UUID foreign keys).
--
-- The vector extension is optional: the embedding column is only used
-- for task-similarity analysis. contextmesh.db.migrate() tolerates a
-- failed CREATE EXTENSION and skips the vector column/index.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS compression_traces (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_args_hash TEXT,
    chunk_ids_selected TEXT[] NOT NULL DEFAULT '{}',
    chunk_ids_pruned TEXT[] NOT NULL DEFAULT '{}',
    original_token_count INTEGER NOT NULL,
    compressed_token_count INTEGER NOT NULL,
    compression_ratio REAL NOT NULL,
    chunk_types_selected TEXT[] NOT NULL DEFAULT '{}',
    chunk_types_pruned TEXT[] NOT NULL DEFAULT '{}',
    low_signal BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT compression_ratio_check CHECK (compression_ratio >= 0 AND compression_ratio <= 1)
);

CREATE INDEX IF NOT EXISTS idx_traces_session_id ON compression_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_traces_task_id ON compression_traces(task_id);
CREATE INDEX IF NOT EXISTS idx_traces_tool_name ON compression_traces(tool_name);
CREATE INDEX IF NOT EXISTS idx_traces_created_at ON compression_traces(created_at);

ALTER TABLE compression_traces ADD COLUMN IF NOT EXISTS task_description_embedding VECTOR(384);

CREATE TABLE IF NOT EXISTS extraction_guidelines (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    score_multiplier REAL NOT NULL DEFAULT 1.0,
    update_count INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evidence_task_ids TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT tool_chunk_unique UNIQUE (tool_name, chunk_type),
    CONSTRAINT multiplier_range CHECK (score_multiplier >= 1.0 AND score_multiplier <= 3.0)
);

CREATE INDEX IF NOT EXISTS idx_guidelines_tool ON extraction_guidelines(tool_name);

CREATE TABLE IF NOT EXISTS guideline_history (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    old_multiplier REAL NOT NULL,
    new_multiplier REAL NOT NULL,
    trigger_task_id TEXT,
    trigger_failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_history_tool_chunk ON guideline_history(tool_name, chunk_type);
CREATE INDEX IF NOT EXISTS idx_history_created ON guideline_history(created_at);

CREATE TABLE IF NOT EXISTS task_outcomes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    session_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed', 'unknown')),
    failure_reason TEXT,
    evaluation_score REAL,
    agent_final_output TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_task_id ON task_outcomes(task_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_session ON task_outcomes(session_id);

CREATE TABLE IF NOT EXISTS failed_tasks (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    session_id TEXT,
    failure_reason TEXT NOT NULL,
    compression_implicated BOOLEAN NOT NULL DEFAULT FALSE,
    root_cause_chunks TEXT[],
    root_cause_chunk_types TEXT[],
    analysis_completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_failed_tasks_session ON failed_tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_failed_tasks_analysis ON failed_tasks(analysis_completed);
