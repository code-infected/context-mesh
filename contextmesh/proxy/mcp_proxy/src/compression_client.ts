import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROTO_PATH = join(__dirname, "../proto/compression.proto");

export interface CompressRequest {
  session_id: string;
  task_id: string;
  tool_name: string;
  tool_args_json: string;
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
  trace_id: string;
  chunk_types_selected: string[];
}

export interface HealthResponse {
  status: string;
}

export class CompressionClient {
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
    const compressionProto = (protoDescriptor as any).contextmesh;

    // Match the server's raised limits: giant tool outputs exceed
    // gRPC's 4MB default message size.
    const messageLimit = 64 * 1024 * 1024;
    this.client = new compressionProto.CompressionService(
      `${host}:${port}`,
      grpc.credentials.createInsecure(),
      {
        "grpc.max_receive_message_length": messageLimit,
        "grpc.max_send_message_length": messageLimit,
      }
    );
  }

  async compress(request: {
    sessionId: string;
    taskId: string;
    toolName: string;
    rawOutput: string;
    taskDescription: string;
    budget: number;
    toolArgs?: Record<string, unknown>;
    recentSteps?: string[];
  }): Promise<CompressResponse> {
    const req: CompressRequest = {
      session_id: request.sessionId,
      task_id: request.taskId,
      tool_name: request.toolName,
      tool_args_json: JSON.stringify(request.toolArgs || {}),
      raw_output: request.rawOutput,
      task_description: request.taskDescription,
      recent_steps: request.recentSteps || [],
      budget_tokens: request.budget,
    };

    return new Promise((resolve, reject) => {
      this.client.Compress(req, (error: grpc.ServiceError | null, response: CompressResponse) => {
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
      this.client.Health({}, (error: grpc.ServiceError | null, response: HealthResponse) => {
        if (error || response?.status !== "healthy") {
          resolve(false);
        } else {
          resolve(true);
        }
      });
    });
  }

  close(): void {
    this.client.close();
  }
}
