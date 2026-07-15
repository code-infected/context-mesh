/**
 * ContextMesh TypeScript SDK.
 *
 * Wraps tool outputs in custom agent loops with task-conditioned
 * compression. Talks HTTP to the ContextMesh API (the dashboard
 * backend's POST /api/compress endpoint, default port 8082).
 *
 * Fail-open: any transport or server error returns the original
 * output unchanged, with metadata.compressionFailed = true.
 *
 * Usage:
 *   const cm = new ContextMesh({
 *     taskDescription: "refactor authentication module to use JWT",
 *     budgetTokens: 8000,
 *   });
 *   const { content, metadata } = await cm.compress({
 *     output: raw, toolName: "read_file", toolArgs: { path: "/src/auth.py" },
 *   });
 */

import type {
  CompressionResult,
  CompressRequest,
  ContextMeshOptions,
  ReportOutcomeRequest,
} from "./models.js";

export * from "./models.js";

function randomId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

export class ContextMesh {
  private taskDescription: string;
  private budgetTokens: number;
  private apiUrl: string;
  private sessionId: string;
  private recentSteps: string[] = [];
  private taskCounter = 0;

  constructor(options: ContextMeshOptions) {
    this.taskDescription = options.taskDescription;
    this.budgetTokens = options.budgetTokens ?? 8000;
    this.apiUrl = (options.apiUrl ?? "http://localhost:8082").replace(/\/$/, "");
    this.sessionId = options.sessionId ?? randomId("session");
  }

  /** Update the task description as the agent's focus evolves. */
  setTaskDescription(taskDescription: string): void {
    this.taskDescription = taskDescription;
  }

  /** Record an agent reasoning step (last 3 are sent with each call). */
  addStep(step: string): void {
    this.recentSteps.push(step);
    if (this.recentSteps.length > 3) {
      this.recentSteps.shift();
    }
  }

  async compress(request: CompressRequest): Promise<CompressionResult> {
    const taskId = request.taskId ?? `task-${this.sessionId}-${this.taskCounter++}`;

    try {
      const response = await fetch(`${this.apiUrl}/api/compress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: this.sessionId,
          task_id: taskId,
          tool_name: request.toolName,
          tool_args: request.toolArgs ?? {},
          raw_output: request.output,
          task_description: this.taskDescription,
          recent_steps: this.recentSteps,
          budget_tokens: request.budgetTokens ?? this.budgetTokens,
        }),
      });

      if (!response.ok) {
        throw new Error(`ContextMesh API returned ${response.status}`);
      }

      const data = (await response.json()) as Record<string, unknown>;

      return {
        content: String(data.compressed_output ?? request.output),
        metadata: {
          originalTokens: Number(data.original_tokens ?? 0),
          compressedTokens: Number(data.compressed_tokens ?? 0),
          compressionRatio: Number(data.compression_ratio ?? 1),
          chunksSelected: Number(data.chunks_selected ?? 0),
          chunksTotal: Number(data.chunks_total ?? 0),
          traceId: typeof data.trace_id === "string" ? data.trace_id : undefined,
        },
      };
    } catch (error) {
      console.error("ContextMesh compression failed; returning raw output:", error);
      return {
        content: request.output,
        metadata: {
          originalTokens: 0,
          compressedTokens: 0,
          compressionRatio: 1.0,
          chunksSelected: 0,
          chunksTotal: 0,
          compressionFailed: true,
        },
      };
    }
  }

  /** Report a task outcome so the ACON loop can learn from failures. */
  async reportOutcome(request: ReportOutcomeRequest): Promise<boolean> {
    try {
      const response = await fetch(
        `${this.apiUrl}/api/tasks/${encodeURIComponent(request.taskId)}/outcome`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task_id: request.taskId,
            session_id: this.sessionId,
            outcome: request.outcome,
            failure_reason: request.failureReason,
            evaluation_score: request.evaluationScore,
            agent_final_output: request.agentFinalOutput,
          }),
        }
      );
      return response.ok;
    } catch (error) {
      console.error("ContextMesh outcome report failed:", error);
      return false;
    }
  }
}
