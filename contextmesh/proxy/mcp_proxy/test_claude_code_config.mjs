// Verifies the exact server config from ..\..\..\..\.mcp.json:
// spawns the proxy the way Claude Code would and drives one tool call.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const transport = new StdioClientTransport({
  command: "node",
  args: [
    "C:\\Users\\Admin\\Desktop\\ContextMesh\\context-mesh\\contextmesh\\proxy\\mcp_proxy\\dist\\index.js",
  ],
  env: {
    ...process.env,
    CONTEXTMESH_UPSTREAM_COMMAND:
      "npx -y @modelcontextprotocol/server-filesystem C:\\Users\\Admin\\Desktop\\ContextMesh",
    CONTEXTMESH_GRPC_HOST: "localhost",
    CONTEXTMESH_GRPC_PORT: "50051",
    CONTEXTMESH_DEFAULT_BUDGET_TOKENS: "2000",
    CONTEXTMESH_TOOL_BUDGETS:
      '{"read_file": 2000, "read_text_file": 2000, "read_media_file": 2000}',
  },
});

const client = new Client({ name: "claude-code-sim", version: "1.0" }, { capabilities: {} });
await client.connect(transport);

const { tools } = await client.listTools();
console.log(`tools visible to Claude Code: ${tools.length}`);
console.log(`includes contextmesh_set_task: ${tools.some((t) => t.name === "contextmesh_set_task")}`);

await client.callTool({
  name: "contextmesh_set_task",
  arguments: { task_description: "find the token expiry logic in the auth service" },
});

const readTool = tools.find((t) => t.name === "read_text_file" || t.name === "read_file").name;
const result = await client.callTool({
  name: readTool,
  arguments: {
    path: "C:\\Users\\Admin\\Desktop\\ContextMesh\\context-mesh\\contextmesh\\benchmarks\\real_agent\\corpus\\auth_service.py",
  },
});

const meta = result._meta?.contextmesh;
console.log(`read via proxy: ${result.content[0].text.length} chars returned`);
console.log(`compression: ${JSON.stringify(meta)}`);
console.log(
  meta && meta.compression_ratio < 1
    ? "CLAUDE CODE CONFIG VERIFIED — compression active"
    : "config works but compression skipped (check gRPC service)"
);
await client.close();
process.exit(0);
