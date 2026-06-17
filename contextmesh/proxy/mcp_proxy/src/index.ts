import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableSSEServerTransport } from "@modelcontextprotocol/sdk/server/streamable-sse.js";
import { compressionClient } from "./compression_client.js";
import { sessionManager } from "./session_manager.js";
import { config } from "./config.js";

export interface ContextMeshProxyOptions {
  upstreamUrl: string;
  port?: number;
  grpcHost?: string;
  grpcPort?: number;
}

export class ContextMeshProxy {
  private server: McpServer;
  private upstreamUrl: string;

  constructor(options: ContextMeshProxyOptions) {
    this.upstreamUrl = options.upstreamUrl;
    this.server = new McpServer({
      name: "contextmesh-proxy",
      version: "0.1.0",
    });

    this.setupToolHandlers();
  }

  private setupToolHandlers() {
    this.server.tool(
      "call_tool",
      async (args: { name: string; arguments: Record<string, unknown> }) => {
        const toolName = args.name;
        const toolArgs = args.arguments;

        const rawResult = await this.forwardToUpstream(toolName, toolArgs);

        const session = sessionManager.getCurrentSession();
        const taskContext = session?.getTaskContext();

        if (!taskContext || !rawResult.content) {
          return rawResult;
        }

        try {
          const compressed = await compressionClient.compress({
            toolName,
            rawOutput: rawResult.content,
            taskContext,
            budget: config.budgetForTool(toolName),
          });

          return {
            ...rawResult,
            content: compressed.compressed_output,
            _contextmesh: {
              original_tokens: compressed.original_tokens,
              compressed_tokens: compressed.compressed_tokens,
              compression_ratio: compressed.compression_ratio,
              chunks_selected: compressed.chunks_selected,
              chunks_total: compressed.chunks_total,
            },
          };
        } catch (error) {
          console.error("Compression failed, returning raw result:", error);
          return rawResult;
        }
      }
    );
  }

  private async forwardToUpstream(
    toolName: string,
    args: Record<string, unknown>
  ): Promise<{ content: string; isError?: boolean }> {
    const response = await fetch(this.upstreamUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: "tools/call", params: { name: toolName, arguments: args } }),
    });

    const data = await response.json();
    return { content: data.result?.content?.[0]?.text || "", isError: data.error };
  }

  async start(port: number = 8081) {
    const transport = new StreamableSSEServerTransport();
    await this.server.connect(transport);
    console.log(`ContextMesh proxy started on port ${port}`);
  }
}

export default ContextMeshProxy;
