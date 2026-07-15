/** Type definitions for the ContextMesh TypeScript SDK. */

export interface CompressionMetadata {
  originalTokens: number;
  compressedTokens: number;
  compressionRatio: number;
  chunksSelected: number;
  chunksTotal: number;
  traceId?: string;
  /** True when compression failed or was skipped and the raw output was returned. */
  compressionFailed?: boolean;
}

export interface CompressionResult {
  content: string;
  metadata: CompressionMetadata;
}

export interface ContextMeshOptions {
  /** What the agent is trying to accomplish; drives relevance scoring. */
  taskDescription: string;
  /** Default token budget per compressed tool output. */
  budgetTokens?: number;
  /**
   * ContextMesh API base URL (the dashboard backend), e.g.
   * "http://localhost:8082". The SDK calls POST {apiUrl}/api/compress.
   */
  apiUrl?: string;
  /** Session identifier; generated when omitted. */
  sessionId?: string;
}

export interface CompressRequest {
  output: string;
  toolName: string;
  toolArgs?: Record<string, unknown>;
  budgetTokens?: number;
  taskId?: string;
}

export type TaskOutcome = "success" | "failed" | "unknown";

export interface ReportOutcomeRequest {
  taskId: string;
  outcome: TaskOutcome;
  failureReason?: string;
  evaluationScore?: number;
  agentFinalOutput?: string;
}
