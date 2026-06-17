"""PostgreSQL schema for ContextMesh feedback and trace storage.

This schema supports:
- compression_traces: Every compression event for analysis
- extraction_guidelines: ACON-learned score multipliers
- sessions: Agent session tracking
- guideline_history: Audit trail for guideline changes
"""

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE outcome_type AS ENUM ('success', 'failed', 'unknown');
CREATE TYPE chunk_format AS ENUM ('code', 'json', 'log', 'html', 'csv', 'shell', 'text');

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task_description TEXT NOT NULL,
    agent_id TEXT,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS compression_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_args_hash TEXT,
    chunk_ids_selected UUID[] NOT NULL,
    chunk_ids_pruned UUID[] NOT NULL,
    original_token_count INTEGER NOT NULL,
    compressed_token_count INTEGER NOT NULL,
    compression_ratio REAL NOT NULL,
    task_description_embedding VECTOR(384),
    chunk_types_selected TEXT[] NOT NULL,
    chunk_types_pruned TEXT[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT compression_ratio_check CHECK (compression_ratio >= 0 AND compression_ratio <= 1)
);

CREATE INDEX idx_traces_session_id ON compression_traces(session_id);
CREATE INDEX idx_traces_task_id ON compression_traces(task_id);
CREATE INDEX idx_traces_tool_name ON compression_traces(tool_name);
CREATE INDEX idx_traces_created_at ON compression_traces(created_at);
CREATE INDEX idx_traces_embedding ON compression_traces USING ivfflat (task_description_embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS extraction_guidelines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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

CREATE INDEX idx_guidelines_tool ON extraction_guidelines(tool_name);
CREATE INDEX idx_guidelines_multiplier ON extraction_guidelines(score_multiplier);

CREATE TABLE IF NOT EXISTS guideline_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guideline_id UUID NOT NULL REFERENCES extraction_guidelines(id) ON DELETE CASCADE,
    old_multiplier REAL NOT NULL,
    new_multiplier REAL NOT NULL,
    trigger_task_id TEXT,
    trigger_failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_history_guideline ON guideline_history(guideline_id);
CREATE INDEX idx_history_created ON guideline_history(created_at);

CREATE TABLE IF NOT EXISTS task_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id TEXT NOT NULL UNIQUE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    outcome outcome_type NOT NULL,
    failure_reason TEXT,
    evaluation_score REAL,
    agent_final_output TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_outcomes_task_id ON task_outcomes(task_id);
CREATE INDEX idx_outcomes_session ON task_outcomes(session_id);
CREATE INDEX idx_outcomes_created ON task_outcomes(created_at);

CREATE TABLE IF NOT EXISTS failed_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id TEXT NOT NULL,
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    failure_reason TEXT NOT NULL,
    root_cause_chunks UUID[],
    root_cause_chunk_types TEXT[],
    analysis_completed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_failed_tasks_session ON failed_tasks(session_id);
CREATE INDEX idx_failed_tasks_analysis ON failed_tasks(analysis_completed);

CREATE OR REPLACE FUNCTION update_session_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER session_update_trigger
    BEFORE UPDATE ON sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_session_timestamp();

CREATE OR REPLACE FUNCTION decay_guideline_multipliers()
RETURNS void AS $$
BEGIN
    UPDATE extraction_guidelines
    SET score_multiplier = GREATEST(1.0, score_multiplier * 0.95),
        last_updated = NOW()
    WHERE score_multiplier > 1.0
      AND last_updated < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;
