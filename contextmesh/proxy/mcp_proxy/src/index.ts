import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { CompressionClient } from "./compression_client.js";
import { SessionManager } from "./session_manager.js";
import { config } from "./config.js";

interface UpstreamConfig {
  url: string;
  name: string;
}

interface ContextMeshProxyOptions {
  upstream: UpstreamConfig;
  grpcHost?: string;
  grpcPort?: number;
  port?: number;
}

class ContextMeshProxy {
  private server: Server;
  private compressionClient: CompressionClient;
  private sessionManager: SessionManager;
  private upstream: UpstreamConfig;

  constructor(options: ContextMeshProxyOptions) {
    this.upstream = options.upstream;
    this.compressionClient = new CompressionClient(
      options.grpcHost || "localhost",
      options.grpcPort || 50051
    );
    this.sessionManager = new SessionManager();

    this.server = new Server(
      {
        name: "contextmesh-proxy",
        version: "0.1.0",
      },
      {
        capabilities: {
          tools: {},
        },
      }
    );

    this.setupHandlers();
  }

  private setupHandlers() {
    this.server.setRequestHandler(ListToolsRequestSchema, async () => {
      return {
        tools: [
          {
            name: "compress",
            description: "Compress tool output using ContextMesh",
            inputSchema: {
              type: "object",
              properties: {
                tool_name: { type: "string" },
                raw_output: { type: "string" },
                task_description: { type: "string" },
                budget_tokens: { type: "number" },
              },
              required: ["tool_name", "raw_output", "task_description"],
            },
          },
        ],
      };
    });

    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      if (request.params.name === "compress") {
        const args = request.params.arguments as Record<string, unknown>;
        return this.handleCompress(args);
      }

      return {
        content: [
          {
            type: "text",
            text: `Unknown tool: ${request.params.name}`,
          },
        ],
        isError: true,
      };
    });
  }

  private async handleCompress(args: Record<string, unknown>) {
    const toolName = args.tool_name as string;
    const rawOutput = args.raw_output as string;
    const taskDescription = args.task_description as string;
    const budgetTokens = (args.budget_tokens as number) || config.budgetForTool(toolName);

    const sessionId = this.sessionManager.getOrCreateSession(taskDescription);
    const taskContext = this.sessionManager.getTaskContext(sessionId);

    if (!taskContext) {
      return {
        content: [
          {
            type: "text",
            text: "Failed to create session context",
          },
        ],
        isError: true,
      };
    }

    try {
      const compressed = await this.compressionClient.compress({
        sessionId,
        taskId: `task-${Date.now()}`,
        toolName,
        rawOutput,
        taskDescription: taskContext.taskDescription,
        budget: budgetTokens,
        toolArgs: {},
        recentSteps: taskContext.recentSteps,
      });

      return {
        content: [
          {
            type: "text",
            text: compressed.compressed_output,
          },
        ],
        metadata: {
          original_tokens: compressed.original_tokens,
          compressed_tokens: compressed.compressed_tokens,
          compression_ratio: compressed.compression_ratio,
          chunks_selected: compressed.chunks_selected,
          chunks_total: compressed.chunks_total,
        },
      };
    } catch (error) {
      console.error("Compression failed, returning raw output:", error);
      return {
        content: [
          {
            type: "text",
            text: rawOutput,
          },
        ],
        metadata: {
          compression_failed: true,
          original_tokens: rawOutput.length / 4,
        },
      };
    }
  }

  async start() {
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    console.error("ContextMesh proxy started via stdio");
  }

  async shutdown() {
    this.compressionClient.close();
    await this.server.close();
  }
}

async function main() {
  const upstreamUrl = process.env.CONTEXTMESH_UPSTREAM || "http://localhost:8080";
  const grpcHost = process.env.CONTEXTMESH_GRPC_HOST || "localhost";
  const grpcPort = parseInt(process.env.CONTEXTMESH_GRPC_PORT || "50051");

  const proxy = new ContextMeshProxy({
    upstream: {
      url: upstreamUrl,
      name: "upstream",
    },
    grpcHost,
    grpcPort,
  });

  process.on("SIGINT", async () => {
    await proxy.shutdown();
    process.exit(0);
  });

  process.on("SIGTERM", async () => {
    await proxy.shutdown();
    process.exit(0);
  });

  await proxy.start();
}

main().catch((error) => {
  console.error("Failed to start proxy:", error);
  process.exit(1);
});

export { ContextMeshProxy };
