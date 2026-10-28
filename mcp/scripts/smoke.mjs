// Telemetry smoke test: runs the built server over stdio with telemetry ON,
// makes one tools/call, closes stdin, and fails if the PostHog flush reports an error.
import { spawn } from "node:child_process";

const child = spawn(process.execPath, ["dist/src/index.js"], {
  env: { ...process.env, RECEIPTS_INDEX_TELEMETRY: "1" },
  stdio: ["pipe", "pipe", "pipe"]
});
let out = "";
let err = "";
child.stdout.on("data", (d) => { out += d; });
child.stderr.on("data", (d) => { err += d; });

const send = (m) => child.stdin.write(JSON.stringify(m) + "\n");
send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "ci-telemetry-smoke", version: "0.1.1" } } });
send({ jsonrpc: "2.0", method: "notifications/initialized" });
send({ jsonrpc: "2.0", id: 2, method: "tools/list" });
send({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "receipts_sample", arguments: { context: "CI smoke test of PostHog MCP Analytics for receipts-index-mcp 0.1.1" } } });

const started = new Date().toISOString();
const timer = setInterval(() => {
  if (out.includes('"id":3')) { clearInterval(timer); child.stdin.end(); }
}, 200);
setTimeout(() => { clearInterval(timer); child.stdin.end(); }, 30000);

child.on("exit", (code) => {
  const lines = out.split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const list = lines.find((m) => m.id === 2);
  const call = lines.find((m) => m.id === 3);
  console.log("started_utc", started, "exit", code);
  console.log("tools", list ? list.result.tools.map((t) => t.name).join(",") : "none");
  console.log("call_is_error", call ? Boolean(call.result && call.result.isError) : "no response");
  console.log("stderr_bytes", err.length);
  if (err) console.log(err.slice(0, 2000));
  process.exit(err.includes("send error") || err.includes("shutdown error") ? 1 : 0);
});
