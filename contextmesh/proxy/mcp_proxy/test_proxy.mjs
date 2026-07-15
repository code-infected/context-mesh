// End-to-end proxy test: agent -> ContextMesh proxy -> filesystem MCP server,
// with the Python gRPC compression service running on :50051.
//
// Run from proxy dir:  node test_proxy.mjs
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const FIXTURE_DIR = "C:/Users/Admin/Desktop/ContextMesh/context-mesh/tests/fixtures";

const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["dist/index.js"],
  env: {
    ...process.env,
    CONTEXTMESH_UPSTREAM_COMMAND: `npx -y @modelcontextprotocol/server-filesystem ${FIXTURE_DIR}`,
    CONTEXTMESH_DEFAULT_BUDGET_TOKENS: "1200",
    CONTEXTMESH_TOOL_BUDGETS: '{"read_file": 1200, "read_text_file": 1200}',
    CONTEXTMESH_GRPC_HOST: "localhost",
    CONTEXTMESH_GRPC_PORT: "50051",
  },
});

const client = new Client({ name: "test-agent", version: "0.0.1" }, { capabilities: {} });
await client.connect(transport);

const { tools } = await client.listTools();
console.log(`tools exposed: ${tools.length}`);
const names = tools.map((t) => t.name);
if (!names.includes("contextmesh_set_task")) throw new Error("handshake tool missing");
const readTool = names.find((n) => n === "read_text_file" || n === "read_file");
if (!readTool) throw new Error(`no read tool among: ${names.join(", ")}`);
console.log(`read tool: ${readTool}`);

await client.callTool({
  name: "contextmesh_set_task",
  arguments: { task_description: "find all authentication-related functions" },
});

const start = Date.now();
const result = await client.callTool({
  name: readTool,
  arguments: { path: `${FIXTURE_DIR}/large_python_file.py` },
});
const elapsed = Date.now() - start;

const text = result.content?.[0]?.text ?? "";
const meta = result._meta?.contextmesh;
console.log(`response chars: ${text.length}, elapsed: ${elapsed}ms`);
console.log("contextmesh meta:", JSON.stringify(meta));

if (!meta) throw new Error("no compression metadata — compression did not run");
if (meta.compression_ratio >= 1) throw new Error("no compression achieved");
if (!text.includes("def") && !text.includes("class")) throw new Error("output lost code content");

console.log("PROXY E2E OK");
await client.close();
process.exit(0);
