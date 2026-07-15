// Streamable HTTP e2e test:
//   1. Start the proxy in HTTP server mode (CONTEXTMESH_PROXY_HTTP_PORT)
//      with a filesystem MCP server as upstream.
//   2. Initialize + list tools via StreamableHTTPClientTransport.
//   3. Open a second concurrent session and check it gets its own
//      mcp-session-id.
//   4. Chain a stdio proxy whose upstream is the HTTP proxy's /mcp URL,
//      exercising the StreamableHTTPClientTransport upstream path (the
//      nested contextmesh_set_task collides and must get web__-prefixed).
//
// Run from proxy dir:  node test_proxy_http.mjs
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const PORT = 3917;
const URL_MCP = `http://localhost:${PORT}/mcp`;

const fixtureDir = mkdtempSync(join(tmpdir(), "cm-http-"));
writeFileSync(join(fixtureDir, "marker.txt"), "MARKER_HTTP_UPSTREAM");

const proxyProc = spawn(process.execPath, ["dist/index.js"], {
  env: {
    ...process.env,
    CONTEXTMESH_PROXY_HTTP_PORT: String(PORT),
    CONTEXTMESH_UPSTREAM_COMMAND: `npx -y @modelcontextprotocol/server-filesystem ${fixtureDir}`,
    CONTEXTMESH_GRPC_HOST: "localhost",
    CONTEXTMESH_GRPC_PORT: "50051",
  },
  stdio: ["ignore", "ignore", "pipe"],
});
proxyProc.stderr.on("data", (chunk) => process.stderr.write(`[proxy] ${chunk}`));

async function connectWithRetry(name) {
  for (let attempt = 0; attempt < 60; attempt++) {
    const client = new Client({ name, version: "0.0.1" }, { capabilities: {} });
    const transport = new StreamableHTTPClientTransport(new URL(URL_MCP));
    try {
      await client.connect(transport);
      return { client, transport };
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw new Error("proxy HTTP server did not answer initialize");
}

let failed = false;
try {
  // --- 1+2. Initialize over Streamable HTTP and list tools.
  const { client: clientA, transport: transportA } = await connectWithRetry("http-agent-a");
  console.log(`session A initialized: ${transportA.sessionId}`);
  const { tools } = await clientA.listTools();
  const names = tools.map((t) => t.name);
  console.log(`tools via HTTP: ${tools.length}`);
  if (!names.includes("contextmesh_set_task")) throw new Error("handshake tool missing over HTTP");
  const readTool = names.find((n) => n === "read_text_file" || n === "read_file");
  if (!readTool) throw new Error(`no read tool among: ${names.join(", ")}`);

  const marker = await clientA.callTool({
    name: readTool,
    arguments: { path: join(fixtureDir, "marker.txt") },
  });
  const markerText = marker.content?.[0]?.text ?? "";
  if (!markerText.includes("MARKER_HTTP_UPSTREAM")) {
    throw new Error(`tool call over HTTP broken: ${markerText}`);
  }
  console.log("tool call over HTTP OK");

  // --- 3. Second concurrent session must get its own session id.
  const { client: clientB, transport: transportB } = await connectWithRetry("http-agent-b");
  console.log(`session B initialized: ${transportB.sessionId}`);
  if (!transportA.sessionId || !transportB.sessionId) throw new Error("missing session ids");
  if (transportA.sessionId === transportB.sessionId) throw new Error("sessions not isolated");
  const toolsB = await clientB.listTools();
  if (toolsB.tools.length !== tools.length) throw new Error("session B sees different tools");
  console.log("concurrent sessions OK");

  // --- 4. Chained stdio proxy with the HTTP proxy as Streamable HTTP upstream.
  const chained = new Client({ name: "chained-agent", version: "0.0.1" }, { capabilities: {} });
  await chained.connect(
    new StdioClientTransport({
      command: process.execPath,
      args: ["dist/index.js"],
      env: {
        ...process.env,
        CONTEXTMESH_UPSTREAMS: JSON.stringify([{ name: "web", url: URL_MCP }]),
        CONTEXTMESH_UPSTREAM_COMMAND: "",
        CONTEXTMESH_UPSTREAM: "",
        CONTEXTMESH_GRPC_HOST: "localhost",
        CONTEXTMESH_GRPC_PORT: "50051",
      },
    })
  );
  const chainedTools = await chained.listTools();
  const chainedNames = chainedTools.tools.map((t) => t.name);
  console.log(`tools via chained proxy: ${chainedTools.tools.length}`);
  if (!chainedNames.includes("web__contextmesh_set_task")) {
    throw new Error(`nested handshake tool not prefixed: ${chainedNames.join(", ")}`);
  }
  const chainedRead = chainedNames.find((n) => n === "read_text_file" || n === "read_file");
  if (!chainedRead) throw new Error("no read tool via chained proxy");
  const chainedMarker = await chained.callTool({
    name: chainedRead,
    arguments: { path: join(fixtureDir, "marker.txt") },
  });
  const chainedText = chainedMarker.content?.[0]?.text ?? "";
  if (!chainedText.includes("MARKER_HTTP_UPSTREAM")) {
    throw new Error(`chained HTTP upstream call broken: ${chainedText}`);
  }
  console.log("Streamable HTTP upstream (chained proxy) OK");

  await chained.close();
  await clientA.close();
  await clientB.close();
  console.log("HTTP E2E OK");
} catch (error) {
  failed = true;
  console.error("HTTP E2E FAILED:", error);
} finally {
  proxyProc.kill();
  try {
    rmSync(fixtureDir, { recursive: true, force: true });
  } catch {
    // best effort
  }
}
process.exit(failed ? 1 : 0);
