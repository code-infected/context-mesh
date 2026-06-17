export interface CompressionMetadata {
  originalTokens: number;
  compressedTokens: number;
  compressionRatio: number;
  chunksSelected: number;
  chunksTotal: number;
}

export interface CompressionResult {
  content: string;
  metadata: CompressionMetadata;
}

export interface ContextMeshOptions {
  taskDescription: string;
  budgetTokens?: number;
  grpcHost?: string;
  grpcPort?: number;
}

export class ContextMesh {
  private taskDescription: string;
  private budgetTokens: number;
  private grpcHost: string;
  private grpcPort: number;

  constructor(options: ContextMeshOptions) {
    this.taskDescription = options.taskDescription;
    this.budgetTokens = options.budgetTokens || 8000;
    this.grpcHost = options.grpcHost || "localhost";
    this.grpcPort = options.grpcPort || 50051;
  }

  async compress(request: {
    output: string;
    toolName: string;
    toolArgs?: Record<string, unknown>;
    budgetTokens?: number;
  }): Promise<CompressionResult> {
    try {
      const response = await fetch(`http://${this.grpcHost}:${this.grpcPort}/compress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: request.toolName,
          raw_output: request.output,
          task_description: this.taskDescription,
          tool_args: request.toolArgs || {},
          budget_tokens: request.budgetTokens || this.budgetTokens,
        }),
      });

      const data = await response.json();

      return {
        content: data.compressed_output,
        metadata: {
          originalTokens: data.original_tokens,
          compressedTokens: data.compressed_tokens,
          compressionRatio: data.compression_ratio,
          chunksSelected: data.chunks_selected,
          chunksTotal: data.chunks_total,
        },
      };
    } catch (error) {
      console.error("Compression failed:", error);
      return {
        content: request.output,
        metadata: {
          originalTokens: 0,
          compressedTokens: 0,
          compressionRatio: 1.0,
          chunksSelected: 0,
          chunksTotal: 0,
        },
      };
    }
  }
}
