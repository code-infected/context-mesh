#!/usr/bin/env node
/**
 * ContextMesh MCP proxy.
 *
 * Wraps one or more upstream MCP servers transparently: the agent points
 * at this proxy instead of the raw tool servers. Every upstream tool is
 * re-exposed 1:1 (with a `<name>__` prefix when tool names collide
 * across upstreams); tool responses are intercepted and compressed by
 * the ContextMesh gRPC service before they reach the agent's context.
 * Resources and prompts are merged across upstreams and passed through.
 *
 * Fail-open: if the compression service is unavailable or errors, the
 * raw upstream output is returned unchanged. The proxy never blocks a
 * tool call on compression.
 *
 * Upstream configuration (env):
 *   CONTEXTMESH_UPSTREAMS         JSON array of upstreams, e.g.
 *                                 '[{"name": "fs", "command": "npx -y @modelcontextprotocol/server-filesystem /repo"},
 *                                   {"name": "web", "url": "http://host:8080/mcp"}]'
 *                                 URLs ending in /mcp (or entries with
 *                                 "transport": "http") use Streamable HTTP;
 *                                 other URLs fall back to SSE.
 *   CONTEXTMESH_UPSTREAM_COMMAND  spawn a single stdio MCP server, e.g.
 *                                 "npx -y @modelcontextprotocol/server-filesystem /repo"
 *   CONTEXTMESH_UPSTREAM          SSE URL of a single running MCP server, e.g.
 *                                 "http://localhost:8080/sse"
 *
 * Server transport (env):
 *   CONTEXTMESH_PROXY_HTTP_PORT   serve MCP over Streamable HTTP on this
 *                                 port (one Server per MCP session) instead
 *                                 of the default stdio transport.
 *
 * Compression service (env):
 *   CONTEXTMESH_COMPRESSION_GRPC_HOST / CONTEXTMESH_GRPC_HOST  (default localhost)
 *   CONTEXTMESH_COMPRESSION_GRPC_PORT / CONTEXTMESH_GRPC_PORT  (default 50051)
 *
 * Adaptive budgets (env):
 *   CONTEXTMESH_SESSION_TOKEN_LIMIT  cumulative compressed-token limit per
 *                                    session (default 0 = disabled). Past 50%
 *                                    usage, per-call budgets scale linearly
 *                                    from 1.0 down to the min factor at 100%.
 *   CONTEXTMESH_ADAPTIVE_MIN_FACTOR  floor for the budget scale factor
 *                                    (default 0.25)
 *
 * The agent can steer compression relevance by calling the extra
 * `contextmesh_set_task` tool with its current task description.
 */

import {
  createServer as createHttpServer,
  type IncomingMessage,
  type Server as HttpServer,
  type ServerResponse,
} from "node:http";
import { randomUUID } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import {
  CallToolRequestSchema,
  GetPromptRequestSchema,
  ListPromptsRequestSchema,
  ListResourcesRequestSchema,
  ListResourceTemplatesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
  isInitializeRequest,
  Prompt,
  Resource,
  ResourceTemplate,
  Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { CompressionClient } from "./compression_client.js";
import { SessionManager } from "./session_manager.js";
import { config } from "./config.js";

const SET_TASK_TOOL: Tool = {
  name: "contextmesh_set_task",
  description:
    "Set the current task description for ContextMesh compression. " +
    "Call this once at the start of a task so tool outputs are pruned " +
    "to content relevant to the task.",
  inputSchema: {
    type: "object",
    properties: {
      task_description: {
        type: "string",
        description: "What the agent is currently trying to accomplish",
      },
    },
    required: ["task_description"],
  },
};

// Outputs below this size aren't worth a compression round trip
// (~1000 tokens; the pipeline would skip them anyway).
const MIN_COMPRESS_CHARS = 4000;

interface UpstreamConfig {
  /** Display name; used as the collision prefix (fs -> fs__read_file). */
  name?: string;
  /** stdio command line, e.g. "npx -y @modelcontextprotocol/server-filesystem /repo" */
  command?: string;
  /** URL of a running MCP server: Streamable HTTP or SSE. */
  url?: string;
  /** Force the URL transport; otherwise inferred (/mcp suffix -> http, else sse). */
  transport?: "http" | "sse";
}

interface UpstreamConnection {
  config: UpstreamConfig;
  /** null for the single unnamed upstream from legacy env vars (never prefixed). */
  name: string | null;
  client: Client;
}

/** Where an exposed (possibly prefixed) tool or prompt name actually lives. */
interface UpstreamRoute {
  upstream: UpstreamConnection;
  originalName: string;
}

/** Per-MCP-connection state (one per stdio process / HTTP session). */
interface ProxySessionState {
  /** ContextMesh compression session id, created lazily. */
  sessionId: string | null;
}

interface ContextMeshProxyOptions {
  upstreams: UpstreamConfig[];
  grpcHost?: string;
  grpcPort?: number;
  /** When set, serve MCP over Streamable HTTP on this port instead of stdio. */
  httpPort?: number;
}

export class ContextMeshProxy {
  private upstreamConfigs: UpstreamConfig[];
  private upstreams: UpstreamConnection[] = [];
  private servers: Server[] = [];
  private compressionClient: CompressionClient;
  private sessionManager: SessionManager;
  private httpPort: number | undefined;
  private httpServer: HttpServer | null = null;
  private httpTransports: Map<string, StreamableHTTPServerTransport> = new Map();
  private toolRoutes: Map<string, UpstreamRoute> = new Map();
  private promptRoutes: Map<string, UpstreamRoute> = new Map();
  private resourceRoutes: Map<string, UpstreamConnection> = new Map();
  private taskCounter = 0;

  constructor(options: ContextMeshProxyOptions) {
    this.upstreamConfigs = options.upstreams;
    this.httpPort = options.httpPort;
    this.compressionClient = new CompressionClient(
      options.grpcHost || "localhost",
      options.grpcPort || 50051
    );
    this.sessionManager = new SessionManager(config.sessionTimeoutMinutes);
  }

  /**
   * Build a Server for one MCP connection. Each connection gets its own
   * Server + session state so concurrent HTTP sessions track separate
   * ContextMesh sessions (and separate adaptive budgets).
   */
  private createServer(): Server {
    const server = new Server(
      { name: "contextmesh-proxy", version: "0.1.0" },
      { capabilities: { tools: {}, resources: {}, prompts: {} } }
    );
    const state: ProxySessionState = { sessionId: null };
    this.setupHandlers(server, state);
    this.servers.push(server);
    return server;
  }

  private setupHandlers(server: Server, state: ProxySessionState) {
    server.setRequestHandler(ListToolsRequestSchema, async () => {
      const tools = await this.refreshToolRoutes();
      return { tools: [...tools, SET_TASK_TOOL] };
    });

    server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const toolName = request.params.name;
      const args = (request.params.arguments ?? {}) as Record<string, unknown>;

      if (toolName === SET_TASK_TOOL.name) {
        return this.handleSetTask(args, state);
      }

      let route = this.toolRoutes.get(toolName);
      if (!route) {
        await this.refreshToolRoutes();
        route = this.toolRoutes.get(toolName);
      }
      if (!route) {
        throw new Error(`Unknown tool: ${toolName}`);
      }
      return this.handleProxiedToolCall(route, toolName, args, state);
    });

    server.setRequestHandler(ListResourcesRequestSchema, async () => {
      const resources: Resource[] = [];
      for (const upstream of this.upstreams) {
        if (!this.upstreamSupports(upstream, "resources")) continue;
        try {
          const result = await upstream.client.listResources();
          for (const resource of result.resources) {
            this.resourceRoutes.set(resource.uri, upstream);
            resources.push(resource);
          }
        } catch (error) {
          this.logUpstreamSkip("resources/list", upstream, error);
        }
      }
      return { resources };
    });

    server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
      const uri = request.params.uri;

      // Prefer the upstream that listed this URI; fall back to trying each.
      const listedBy = this.resourceRoutes.get(uri);
      let lastError: unknown = new Error(`No upstream could read resource: ${uri}`);
      if (listedBy) {
        try {
          return await listedBy.client.readResource({ uri });
        } catch (error) {
          lastError = error;
        }
      }
      for (const upstream of this.upstreams) {
        if (upstream === listedBy) continue;
        if (!this.upstreamSupports(upstream, "resources")) continue;
        try {
          return await upstream.client.readResource({ uri });
        } catch (error) {
          lastError = error;
        }
      }
      throw lastError instanceof Error ? lastError : new Error(String(lastError));
    });

    server.setRequestHandler(ListResourceTemplatesRequestSchema, async () => {
      const resourceTemplates: ResourceTemplate[] = [];
      for (const upstream of this.upstreams) {
        if (!this.upstreamSupports(upstream, "resources")) continue;
        try {
          const result = await upstream.client.listResourceTemplates();
          resourceTemplates.push(...result.resourceTemplates);
        } catch (error) {
          this.logUpstreamSkip("resources/templates/list", upstream, error);
        }
      }
      return { resourceTemplates };
    });

    server.setRequestHandler(ListPromptsRequestSchema, async () => {
      const prompts = await this.refreshPromptRoutes();
      return { prompts };
    });

    server.setRequestHandler(GetPromptRequestSchema, async (request) => {
      const promptName = request.params.name;
      let route = this.promptRoutes.get(promptName);
      if (!route) {
        await this.refreshPromptRoutes();
        route = this.promptRoutes.get(promptName);
      }
      if (!route) {
        throw new Error(`Unknown prompt: ${promptName}`);
      }
      return route.upstream.client.getPrompt({
        name: route.originalName,
        arguments: request.params.arguments,
      });
    });
  }

  /**
   * Re-list tools from every upstream and rebuild the routing map.
   * Names that collide across upstreams (or shadow the handshake tool)
   * are exposed as `<upstream>__<tool>`; a single unnamed upstream is
   * never prefixed.
   */
  private async refreshToolRoutes(): Promise<Tool[]> {
    const listings: { upstream: UpstreamConnection; tools: Tool[] }[] = [];
    for (const upstream of this.upstreams) {
      if (!this.upstreamSupports(upstream, "tools")) continue;
      try {
        const result = await upstream.client.listTools();
        listings.push({ upstream, tools: result.tools });
      } catch (error) {
        this.logUpstreamSkip("tools/list", upstream, error);
      }
    }

    const nameCounts = new Map<string, number>();
    for (const { tools } of listings) {
      for (const tool of tools) {
        nameCounts.set(tool.name, (nameCounts.get(tool.name) ?? 0) + 1);
      }
    }

    const routes = new Map<string, UpstreamRoute>();
    const exposed: Tool[] = [];
    for (const { upstream, tools } of listings) {
      for (const tool of tools) {
        const exposedName = this.exposedName(tool.name, upstream, nameCounts);
        routes.set(exposedName, { upstream, originalName: tool.name });
        exposed.push(exposedName === tool.name ? tool : { ...tool, name: exposedName });
      }
    }
    this.toolRoutes = routes;
    return exposed;
  }

  /** Same collision-prefix scheme as tools, applied to prompt names. */
  private async refreshPromptRoutes(): Promise<Prompt[]> {
    const listings: { upstream: UpstreamConnection; prompts: Prompt[] }[] = [];
    for (const upstream of this.upstreams) {
      if (!this.upstreamSupports(upstream, "prompts")) continue;
      try {
        const result = await upstream.client.listPrompts();
        listings.push({ upstream, prompts: result.prompts });
      } catch (error) {
        this.logUpstreamSkip("prompts/list", upstream, error);
      }
    }

    const nameCounts = new Map<string, number>();
    for (const { prompts } of listings) {
      for (const prompt of prompts) {
        nameCounts.set(prompt.name, (nameCounts.get(prompt.name) ?? 0) + 1);
      }
    }

    const routes = new Map<string, UpstreamRoute>();
    const exposed: Prompt[] = [];
    for (const { upstream, prompts } of listings) {
      for (const prompt of prompts) {
        const exposedName = this.exposedName(prompt.name, upstream, nameCounts);
        routes.set(exposedName, { upstream, originalName: prompt.name });
        exposed.push(exposedName === prompt.name ? prompt : { ...prompt, name: exposedName });
      }
    }
    this.promptRoutes = routes;
    return exposed;
  }

  private exposedName(
    originalName: string,
    upstream: UpstreamConnection,
    nameCounts: Map<string, number>
  ): string {
    const collides =
      (nameCounts.get(originalName) ?? 0) > 1 || originalName === SET_TASK_TOOL.name;
    if (collides && upstream.name) {
      return `${upstream.name}__${originalName}`;
    }
    return originalName;
  }

  private upstreamSupports(
    upstream: UpstreamConnection,
    capability: "tools" | "resources" | "prompts"
  ): boolean {
    const capabilities = upstream.client.getServerCapabilities();
    // Unknown capabilities: try anyway and let per-request errors skip it.
    if (!capabilities) return true;
    return capabilities[capability] !== undefined;
  }

  private logUpstreamSkip(
    operation: string,
    upstream: UpstreamConnection,
    error: unknown
  ): void {
    console.error(
      `ContextMesh ${operation} skipped for upstream ${upstream.name ?? "default"}:`,
      error instanceof Error ? error.message : error
    );
  }

  private handleSetTask(args: Record<string, unknown>, state: ProxySessionState) {
    const taskDescription = String(args.task_description ?? "");
    state.sessionId = this.sessionManager.createSession(taskDescription);
    return {
      content: [
        {
          type: "text" as const,
          text: `ContextMesh task set: ${taskDescription}`,
        },
      ],
    };
  }

  /**
   * Scale a tool's configured budget by session usage: full budget up to
   * 50% of CONTEXTMESH_SESSION_TOKEN_LIMIT, then a linear ramp down to
   * adaptiveMinFactor * budget as usage approaches 100% of the limit.
   */
  private effectiveBudgetFor(toolName: string, sessionId: string): number {
    const baseBudget = config.budgetForTool(toolName);
    const limit = config.sessionTokenLimit;
    if (limit <= 0) return baseBudget;

    const used = this.sessionManager.getCompressedTokensUsed(sessionId);
    const usage = used / limit;
    if (usage <= 0.5) return baseBudget;

    const minFactor = config.adaptiveMinFactor;
    const factor = Math.max(minFactor, 1 - ((usage - 0.5) / 0.5) * (1 - minFactor));
    return Math.round(baseBudget * factor);
  }

  private async handleProxiedToolCall(
    route: UpstreamRoute,
    exposedName: string,
    args: Record<string, unknown>,
    state: ProxySessionState
  ) {
    // 1. Forward the call to the owning upstream under its original name.
    const rawResult = await route.upstream.client.callTool({
      name: route.originalName,
      arguments: args,
    });

    // 2. Resolve session/task context.
    if (state.sessionId === null) {
      state.sessionId = this.sessionManager.createSession(
        `Agent session using tool ${exposedName}`
      );
    }
    const taskContext = this.sessionManager.getTaskContext(state.sessionId);
    const taskId = `task-${state.sessionId}-${this.taskCounter++}`;
    const effectiveBudget = this.effectiveBudgetFor(route.originalName, state.sessionId);

    // 3. Compress each large text content item; fail open per item.
    const content = Array.isArray(rawResult.content) ? rawResult.content : [];
    const newContent: unknown[] = [];
    let originalTokens = 0;
    let compressedTokens = 0;
    let compressedAny = false;

    for (const item of content) {
      const isLargeText =
        item &&
        typeof item === "object" &&
        (item as { type?: string }).type === "text" &&
        typeof (item as { text?: string }).text === "string" &&
        (item as { text: string }).text.length >= MIN_COMPRESS_CHARS;

      if (!isLargeText || rawResult.isError) {
        newContent.push(item);
        continue;
      }

      const text = (item as { text: string }).text;
      try {
        const compressed = await this.compressionClient.compress({
          sessionId: state.sessionId,
          taskId,
          toolName: route.originalName,
          rawOutput: text,
          taskDescription: taskContext?.taskDescription ?? "",
          budget: effectiveBudget,
          toolArgs: args,
          recentSteps: taskContext?.recentSteps ?? [],
        });
        newContent.push({ ...(item as object), text: compressed.compressed_output });
        originalTokens += compressed.original_tokens;
        compressedTokens += compressed.compressed_tokens;
        compressedAny = true;
        this.sessionManager.addCompressedTokens(
          state.sessionId,
          Number(compressed.compressed_tokens)
        );
      } catch (error) {
        console.error(
          `ContextMesh compression failed for ${exposedName}; returning raw output:`,
          error instanceof Error ? error.message : error
        );
        newContent.push(item);
      }
    }

    // 4. Track this call as a recent step for evolving task context.
    this.sessionManager.addRecentStep(
      state.sessionId,
      `${exposedName}(${JSON.stringify(args).slice(0, 200)})`
    );

    const result: Record<string, unknown> = {
      ...rawResult,
      content: newContent,
    };
    if (compressedAny) {
      result._meta = {
        ...(rawResult._meta as object | undefined),
        contextmesh: {
          original_tokens: originalTokens,
          compressed_tokens: compressedTokens,
          compression_ratio:
            originalTokens > 0 ? compressedTokens / originalTokens : 1,
          effective_budget: effectiveBudget,
          session_compressed_tokens:
            this.sessionManager.getCompressedTokensUsed(state.sessionId),
        },
      };
    }
    return result;
  }

  private buildUpstreamTransport(upstreamConfig: UpstreamConfig): Transport {
    if (upstreamConfig.command) {
      const parts = upstreamConfig.command.split(/\s+/).filter(Boolean);
      return new StdioClientTransport({
        command: parts[0],
        args: parts.slice(1),
      });
    }
    if (upstreamConfig.url) {
      const url = new URL(upstreamConfig.url);
      const useStreamableHttp =
        upstreamConfig.transport === "http" ||
        (upstreamConfig.transport !== "sse" &&
          url.pathname.replace(/\/+$/, "").endsWith("/mcp"));
      return useStreamableHttp
        ? new StreamableHTTPClientTransport(url)
        : new SSEClientTransport(url);
    }
    throw new Error(
      `Upstream "${upstreamConfig.name ?? "default"}" needs a "command" or "url"`
    );
  }

  private async connectUpstreams(): Promise<void> {
    if (this.upstreamConfigs.length === 0) {
      throw new Error(
        "No upstream configured: set CONTEXTMESH_UPSTREAMS (JSON array), " +
          "CONTEXTMESH_UPSTREAM_COMMAND (stdio) or CONTEXTMESH_UPSTREAM (SSE URL)"
      );
    }
    for (const upstreamConfig of this.upstreamConfigs) {
      const client = new Client(
        { name: "contextmesh-proxy-client", version: "0.1.0" },
        { capabilities: {} }
      );
      await client.connect(this.buildUpstreamTransport(upstreamConfig));
      this.upstreams.push({
        config: upstreamConfig,
        name: upstreamConfig.name ?? null,
        client,
      });
      console.error(
        `ContextMesh proxy connected to upstream${
          upstreamConfig.name ? ` "${upstreamConfig.name}"` : ""
        } (${upstreamConfig.command ?? upstreamConfig.url})`
      );
    }
  }

  /**
   * Streamable HTTP server mode: one Server + transport pair per MCP
   * session, keyed by the mcp-session-id header (stateful, per SDK docs).
   */
  private async handleHttpRequest(
    req: IncomingMessage,
    res: ServerResponse
  ): Promise<void> {
    let parsedBody: unknown = undefined;
    if (req.method === "POST") {
      const chunks: Buffer[] = [];
      for await (const chunk of req) {
        chunks.push(chunk as Buffer);
      }
      const bodyText = Buffer.concat(chunks).toString("utf8");
      if (bodyText.length > 0) {
        try {
          parsedBody = JSON.parse(bodyText);
        } catch {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              jsonrpc: "2.0",
              error: { code: -32700, message: "Parse error" },
              id: null,
            })
          );
          return;
        }
      }
    }

    const sessionHeader = req.headers["mcp-session-id"];
    const sessionId = Array.isArray(sessionHeader) ? sessionHeader[0] : sessionHeader;

    const existing = sessionId ? this.httpTransports.get(sessionId) : undefined;
    if (existing) {
      await existing.handleRequest(req, res, parsedBody);
      return;
    }

    if (req.method === "POST" && !sessionId && isInitializeRequest(parsedBody)) {
      const transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: (newSessionId: string) => {
          this.httpTransports.set(newSessionId, transport);
        },
      });
      // Registered before connect(); the SDK chains it with its own onclose.
      transport.onclose = () => {
        if (transport.sessionId) {
          this.httpTransports.delete(transport.sessionId);
        }
      };
      const server = this.createServer();
      await server.connect(transport);
      await transport.handleRequest(req, res, parsedBody);
      return;
    }

    res.writeHead(400, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        jsonrpc: "2.0",
        error: { code: -32000, message: "Bad Request: no valid session ID provided" },
        id: null,
      })
    );
  }

  private async startHttp(port: number): Promise<void> {
    this.httpServer = createHttpServer((req, res) => {
      this.handleHttpRequest(req, res).catch((error) => {
        console.error("ContextMesh HTTP request failed:", error);
        if (!res.headersSent) {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              jsonrpc: "2.0",
              error: { code: -32603, message: "Internal server error" },
              id: null,
            })
          );
        }
      });
    });

    await new Promise<void>((resolve, reject) => {
      this.httpServer!.once("error", reject);
      this.httpServer!.listen(port, () => resolve());
    });
    console.error(
      `ContextMesh proxy listening on http://localhost:${port}/mcp (Streamable HTTP)`
    );
  }

  async start() {
    await this.connectUpstreams();

    if (this.httpPort && this.httpPort > 0) {
      await this.startHttp(this.httpPort);
      return;
    }

    const server = this.createServer();
    await server.connect(new StdioServerTransport());
    console.error("ContextMesh proxy started via stdio");
  }

  async shutdown() {
    this.compressionClient.close();
    for (const transport of this.httpTransports.values()) {
      await transport.close().catch(() => undefined);
    }
    this.httpTransports.clear();
    if (this.httpServer) {
      await new Promise<void>((resolve) => this.httpServer!.close(() => resolve()));
      this.httpServer = null;
    }
    for (const upstream of this.upstreams) {
      await upstream.client.close().catch(() => undefined);
    }
    for (const server of this.servers) {
      await server.close().catch(() => undefined);
    }
  }
}

function parseUpstreamsFromEnv(): UpstreamConfig[] {
  // CONTEXTMESH_UPSTREAMS='[{"name": "fs", "command": "npx ..."}, {"name": "web", "url": "http://host:8080/mcp"}]'
  const raw = process.env.CONTEXTMESH_UPSTREAMS;
  if (raw) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch (error) {
      throw new Error(
        `Invalid CONTEXTMESH_UPSTREAMS JSON: ${
          error instanceof Error ? error.message : error
        }`
      );
    }
    if (!Array.isArray(parsed)) {
      throw new Error("CONTEXTMESH_UPSTREAMS must be a JSON array of upstreams");
    }
    return parsed.map((entry, index) => {
      const record = (entry ?? {}) as Record<string, unknown>;
      const upstream: UpstreamConfig = {
        name:
          typeof record.name === "string" && record.name.length > 0
            ? record.name
            : `upstream${index + 1}`,
        command: typeof record.command === "string" ? record.command : undefined,
        url: typeof record.url === "string" ? record.url : undefined,
        transport:
          record.transport === "http" || record.transport === "sse"
            ? record.transport
            : undefined,
      };
      if (!upstream.command && !upstream.url) {
        throw new Error(
          `CONTEXTMESH_UPSTREAMS entry ${index} needs a "command" or "url"`
        );
      }
      return upstream;
    });
  }

  // Legacy single upstream: one unnamed upstream, no prefixing.
  const command = process.env.CONTEXTMESH_UPSTREAM_COMMAND;
  const url = process.env.CONTEXTMESH_UPSTREAM;
  if (command || url) {
    return [{ command, url }];
  }
  return [];
}

async function main() {
  const proxy = new ContextMeshProxy({
    upstreams: parseUpstreamsFromEnv(),
    grpcHost:
      process.env.CONTEXTMESH_COMPRESSION_GRPC_HOST ||
      process.env.CONTEXTMESH_GRPC_HOST ||
      "localhost",
    grpcPort: parseInt(
      process.env.CONTEXTMESH_COMPRESSION_GRPC_PORT ||
        process.env.CONTEXTMESH_GRPC_PORT ||
        "50051"
    ),
    httpPort: parseInt(process.env.CONTEXTMESH_PROXY_HTTP_PORT || "") || undefined,
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

const isMain =
  process.argv[1] !== undefined &&
  import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop() ?? "");

if (isMain) {
  main().catch((error) => {
    console.error("Failed to start proxy:", error);
    process.exit(1);
  });
}
