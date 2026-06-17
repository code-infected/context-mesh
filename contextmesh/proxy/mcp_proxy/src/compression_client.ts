import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import path from "path";

const PROTO_PATH = path.join(__dirname, "../proto/compression.proto");

interface CompressRequest {
  session_id: string;
  task_id: string;
  tool_name: string;
  tool_args_json: string;
  raw_output: string;
  task_description: string;
  recent_steps: string[];
  budget_tokens: number;
}

interface CompressResponse {
  compressed_output: string;
  original_tokens: number;
  compressed_tokens: number;
  compression_ratio: number;
  chunks_selected: number;
  chunks_total: number;
  trace_id: string;
  chunk_types_selected: string[];
}

class CompressionClient {
  private client: any;
  private grpcHost: string;
  private grpcPort: number;

  constructor(host: string = "localhost", port: number = 50051) {
    this.grpcHost = host;
    this.grpcPort = port;

    const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
      keepCase: true,
      longs: String,
      enums: String,
      defaults: true,
      oneofs: true,
    });

    const protoDescriptor = grpc.loadPackageDefinition(packageDefinition);
    const compressionProto = protoDescriptor.contextmesh as any;

    this.client = new compressionProto.CompressionService(
      `${host}:${port}`,
      grpc.credentials.createInsecure()
    );
  }

  async compress(request: {
    toolName: string;
    rawOutput: string;
    taskContext: { taskDescription: string; recentSteps: string[] };
    budget: number;
    sessionId?: string;
    taskId?: string;
    toolArgs?: Record<string, unknown>;
  }): Promise<CompressResponse> {
    const req: CompressRequest = {
      session_id: request.sessionId || "unknown",
      task_id: request.taskId || "unknown",
      tool_name: request.toolName,
      tool_args_json: JSON.stringify(request.toolArgs || {}),
      raw_output: request.rawOutput,
      task_description: request.taskContext.taskDescription,
      recent_steps: request.taskContext.recentSteps,
      budget_tokens: request.budget,
    };

    return new Promise((resolve, reject) => {
      this.client.Compress(req, (error: any, response: CompressResponse) => {
        if (error) {
          reject(error);
        } else {
          resolve(response);
        }
      });
    });
  }

  async healthCheck(): Promise<boolean> {
    return new Promise((resolve) => {
      this.client.Health({}, (error: any, response: { status: string }) => {
        if (error || response?.status !== "healthy") {
          resolve(false);
        } else {
          resolve(true);
        }
      });
    });
  }
}

export const compressionClient = new CompressionClient();
export { CompressionClient };
