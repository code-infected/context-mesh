/**
 * Typed client for the ContextMesh dashboard API (FastAPI backend on :8082,
 * reached through the Vite dev-server proxy at /api).
 *
 * Types mirror the backend contract exactly.
 */

export interface HealthResponse {
  status: string;
  traces_stored: number;
  sessions: number;
}

export interface SessionSummary {
  session_id: string;
  task_count: number;
  trace_count: number;
  original_tokens: number;
  compressed_tokens: number;
  tokens_saved: number;
  avg_compression_ratio: number;
  first_seen: string;
  last_seen: string;
}

export interface SessionsResponse {
  sessions: SessionSummary[];
}

export interface CompressionTrace {
  trace_id: string;
  session_id: string;
  task_id: string;
  tool_name: string;
  timestamp: string;
  original_tokens: number;
  compressed_tokens: number;
  compression_ratio: number;
  chunks_selected: number;
  chunks_total: number;
  chunk_types_selected: string[];
  low_signal: boolean;
}

export interface TracesResponse {
  traces: CompressionTrace[];
}

export interface ToolStats {
  tool_name: string;
  call_count: number;
  avg_compression_ratio: number;
  original_tokens: number;
  compressed_tokens: number;
  tokens_saved: number;
  avg_chunks_selected: number;
  avg_chunks_total: number;
  failure_count: number;
}

export interface ToolStatsResponse {
  tools: ToolStats[];
}

export interface Guideline {
  tool_name: string;
  chunk_type: string;
  score_multiplier: number;
  update_count: number;
  last_updated: string;
  evidence_task_ids: string[];
}

export interface GuidelinesResponse {
  guidelines: Guideline[];
}

export interface GuidelineUpdate {
  tool_name: string;
  chunk_type: string;
  old_multiplier: number;
  new_multiplier: number;
  task_id: string;
  timestamp: string;
}

export interface GuidelineHistoryResponse {
  history: GuidelineUpdate[];
}

export interface FailureRecord {
  task_id: string;
  session_id: string;
  failure_reason: string;
  compression_implicated: boolean;
  timestamp: string;
  pruned_chunk_types: string[];
  trace_ids: string[];
}

export interface FailuresResponse {
  failures: FailureRecord[];
}

export interface DiffChunk {
  chunk_id: string;
  chunk_type: string;
  token_count: number;
  selected: boolean;
  score: number | null;
  preview: string;
}

export interface TraceDiff {
  trace_id: string;
  session_id: string;
  task_id: string;
  tool_name: string;
  timestamp: string;
  original_tokens: number;
  compressed_tokens: number;
  compression_ratio: number;
  chunks: DiffChunk[];
}

/** Aggregate KPIs served by GET /api/stats/overview (single call, no fan-out). */
export interface OverviewStats {
  sessions: number;
  traces: number;
  tasks: number;
  original_tokens: number;
  compressed_tokens: number;
  tokens_saved: number;
  avg_compression_ratio: number;
  low_signal_traces: number;
  failures: number;
  compression_implicated_failures: number;
  guidelines_active: number;
  scorer_fallback: boolean;
  trace_backend: string;
}

/** Body for POST /api/compress (interactive playground). */
export interface CompressRequest {
  session_id: string;
  task_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  raw_output: string;
  task_description: string;
  recent_steps: string[];
  budget_tokens: number;
}

export interface CompressResponse {
  compressed_output: string;
  original_tokens: number;
  compressed_tokens: number;
  compression_ratio: number;
  chunks_selected: number;
  chunks_total: number;
  /** Null when no trace was recorded (small outputs, fail-open paths). */
  trace_id: string | null;
  chunk_types_selected: string[];
}

/**
 * Error carrying the HTTP status and the backend's `detail` message (FastAPI
 * error bodies are `{"detail": "..."}`), so callers can special-case e.g. 404.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(message: string, status: number, detail: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseJson<T>(res: Response, path: string): Promise<T> {
  if (!res.ok) {
    let detail: string | null = null;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body — fall through to the generic message */
    }
    throw new ApiError(
      detail ?? `API request failed: ${res.status} ${res.statusText} (${path})`,
      res.status,
      detail,
    );
  }
  return (await res.json()) as T;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  return parseJson<T>(res, path);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  return parseJson<T>(res, path);
}

export const api = {
  health: () => getJson<HealthResponse>("/api/health"),
  sessions: () => getJson<SessionsResponse>("/api/sessions"),
  sessionTraces: (sessionId: string) =>
    getJson<TracesResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/traces`),
  traceDiff: (traceId: string) =>
    getJson<TraceDiff>(`/api/traces/${encodeURIComponent(traceId)}/diff`),
  toolStats: () => getJson<ToolStatsResponse>("/api/tools/stats"),
  statsOverview: () => getJson<OverviewStats>("/api/stats/overview"),
  compress: (body: CompressRequest) => postJson<CompressResponse>("/api/compress", body),
  guidelines: () => getJson<GuidelinesResponse>("/api/guidelines"),
  guidelineHistory: () => getJson<GuidelineHistoryResponse>("/api/guidelines/history"),
  failures: () => getJson<FailuresResponse>("/api/failures"),
};
