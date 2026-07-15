// Multi-upstream + adaptive-budget e2e test:
// agent -> ContextMesh proxy -> two filesystem MCP servers rooted at
// different directories, with the Python gRPC compression service on :50051.
//
// Verifies:
//   1. Colliding tool names are exposed as fs1__* / fs2__* and route to
//      the correct upstream (checked with per-upstream marker files).
//   2. Resources/prompts passthrough answers without error even though
//      the upstreams lack those capabilities.
//   3. Adaptive budgets: with CONTEXTMESH_SESSION_TOKEN_LIMIT set low, the
//      second large read gets a smaller effective budget via _meta.
//
// Run from proxy dir:  node test_proxy_multi.mjs
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// --- Fixtures: two roots with distinct markers and large compressible files.
const dirA = mkdtempSync(join(tmpdir(), "cm-multi-a-"));
const dirB = mkdtempSync(join(tmpdir(), "cm-multi-b-"));
writeFileSync(join(dirA, "marker.txt"), "MARKER_UPSTREAM_A");
writeFileSync(join(dirB, "marker.txt"), "MARKER_UPSTREAM_B");

// Heterogeneous content: a few auth-related functions buried in unrelated
// helpers, so relevance scoring has signal and compression actually runs.
const TOPICS = [
  ["authenticate_user", "Verify the password hash and issue a signed session token"],
  ["render_revenue_chart", "Draw the quarterly revenue chart as an SVG document"],
  ["parse_inventory_csv", "Split raw warehouse CSV rows into typed inventory records"],
  ["send_digest_email", "Deliver the weekly digest email through the SMTP relay"],
  ["resize_thumbnail", "Scale an uploaded image down to the thumbnail bounding box"],
  ["validate_login_token", "Check token signature, expiry and revocation for login"],
  ["compute_shipping_cost", "Estimate parcel shipping cost from weight and distance"],
  ["migrate_legacy_rows", "Copy legacy database rows into the new schema tables"],
];

function largePython(tag) {
  let out = `"""Synthetic mixed-purpose module for ${tag}."""\n`;
  for (let i = 0; i < 40; i++) {
    const [fn, doc] = TOPICS[i % TOPICS.length];
    out +=
      `\ndef ${tag}_${fn}_${i}(payload, retries=${i % 5}):\n` +
      `    """${doc} (variant ${i} for ${tag})."""\n` +
      `    state = initialize_${fn}_state(payload, seed=${i * 31 + 7})\n` +
      `    for attempt in range(retries + 1):\n` +
      `        outcome = process_${fn}(state, attempt, threshold=${(i % 9) + 1} * 0.${(i % 7) + 1})\n` +
      `        if outcome.status == "done":\n` +
      `            record_metric("${tag}.${fn}.${i}", outcome.elapsed_ms)\n` +
      `            return outcome.value\n` +
      `    raise RuntimeError("${fn} variant ${i} exhausted retries")\n`;
  }
  return out;
}
writeFileSync(join(dirA, "large_a.py"), largePython("alpha"));
writeFileSync(join(dirB, "large_b.py"), largePython("beta"));

const upstreams = JSON.stringify([
  { name: "fs1", command: `npx -y @modelcontextprotocol/server-filesystem ${dirA}` },
  { name: "fs2", command: `npx -y @modelcontextprotocol/server-filesystem ${dirB}` },
]);

const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["dist/index.js"],
  env: {
    ...process.env,
    CONTEXTMESH_UPSTREAMS: upstreams,
    CONTEXTMESH_UPSTREAM_COMMAND: "",
    CONTEXTMESH_UPSTREAM: "",
    CONTEXTMESH_DEFAULT_BUDGET_TOKENS: "1200",
    CONTEXTMESH_TOOL_BUDGETS: '{"read_file": 1200, "read_text_file": 1200}',
    CONTEXTMESH_SESSION_TOKEN_LIMIT: "600",
    CONTEXTMESH_ADAPTIVE_MIN_FACTOR: "0.25",
    CONTEXTMESH_GRPC_HOST: "localhost",
    CONTEXTMESH_GRPC_PORT: "50051",
  },
});

const client = new Client({ name: "test-agent-multi", version: "0.0.1" }, { capabilities: {} });
await client.connect(transport);

// --- 1. Prefixed listing.
const { tools } = await client.listTools();
const names = tools.map((t) => t.name);
console.log(`tools exposed: ${tools.length}`);
if (!names.includes("contextmesh_set_task")) throw new Error("handshake tool missing");

const readA = names.find((n) => n === "fs1__read_text_file" || n === "fs1__read_file");
const readB = names.find((n) => n === "fs2__read_text_file" || n === "fs2__read_file");
if (!readA || !readB) throw new Error(`prefixed read tools missing among: ${names.join(", ")}`);
const bareRead = names.find((n) => n === "read_text_file" || n === "read_file");
if (bareRead) throw new Error(`colliding tool exposed without prefix: ${bareRead}`);
console.log(`prefixed read tools: ${readA}, ${readB}`);

// --- 2. Routing: each prefixed tool must hit its own root.
const markerA = await client.callTool({
  name: readA,
  arguments: { path: join(dirA, "marker.txt") },
});
const markerB = await client.callTool({
  name: readB,
  arguments: { path: join(dirB, "marker.txt") },
});
const textA = markerA.content?.[0]?.text ?? "";
const textB = markerB.content?.[0]?.text ?? "";
if (!textA.includes("MARKER_UPSTREAM_A")) throw new Error(`fs1 routing broken: ${textA}`);
if (!textB.includes("MARKER_UPSTREAM_B")) throw new Error(`fs2 routing broken: ${textB}`);
console.log("routing OK: fs1 -> MARKER_UPSTREAM_A, fs2 -> MARKER_UPSTREAM_B");

// --- 3. Resources/prompts passthrough (upstreams lack these; must not error).
const { resources } = await client.listResources();
const { prompts } = await client.listPrompts();
console.log(`resources merged: ${resources.length}, prompts merged: ${prompts.length}`);

// --- 4. Adaptive budgets: fresh session, two large compressed reads.
// Warm the pipeline's embedding cache on this exact content first so the
// asserted reads cannot hit the cold-encode hard timeout (fail-open).
for (const [tool, path] of [
  [readA, join(dirA, "large_a.py")],
  [readB, join(dirB, "large_b.py")],
]) {
  await client.callTool({ name: tool, arguments: { path } });
}

await client.callTool({
  name: "contextmesh_set_task",
  arguments: { task_description: "find all authentication-related functions" },
});

const first = await client.callTool({
  name: readA,
  arguments: { path: join(dirA, "large_a.py") },
});
const metaFirst = first._meta?.contextmesh;
console.log("first read meta:", JSON.stringify(metaFirst));
if (!metaFirst) throw new Error("first read: no compression metadata");
if (metaFirst.effective_budget !== 1200) {
  throw new Error(`first read should get full budget 1200, got ${metaFirst.effective_budget}`);
}
if (metaFirst.compression_ratio >= 1) throw new Error("first read: no compression achieved");

const second = await client.callTool({
  name: readB,
  arguments: { path: join(dirB, "large_b.py") },
});
const metaSecond = second._meta?.contextmesh;
console.log("second read meta:", JSON.stringify(metaSecond));
if (!metaSecond) throw new Error("second read: no compression metadata");
if (!(metaSecond.effective_budget < metaFirst.effective_budget)) {
  throw new Error(
    `adaptive scaling did not kick in: ${metaSecond.effective_budget} >= ${metaFirst.effective_budget}`
  );
}
const floor = Math.round(0.25 * 1200);
if (metaSecond.effective_budget < floor) {
  throw new Error(`budget below min-factor floor ${floor}: ${metaSecond.effective_budget}`);
}
console.log(
  `adaptive budget OK: ${metaFirst.effective_budget} -> ${metaSecond.effective_budget} (floor ${floor})`
);

console.log("MULTI-UPSTREAM E2E OK");
await client.close();
for (const dir of [dirA, dirB]) {
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    // best effort; Windows may still hold a handle
  }
}
process.exit(0);
