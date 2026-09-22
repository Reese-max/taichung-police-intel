import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../", import.meta.url));

function findPython() {
  const candidates = process.platform === "win32"
    ? [{ command: "python", prefix: [] }, { command: "py", prefix: ["-3"] }]
    : [{ command: "python3", prefix: [] }, { command: "python", prefix: [] }];
  for (const candidate of candidates) {
    const result = spawnSync(candidate.command, [...candidate.prefix, "--version"], { stdio: "ignore", timeout: 10000 });
    if (result.status === 0) return candidate;
  }
  throw new Error("No supported Python interpreter found");
}

test("read-only Query Gateway HTTP/MCP parity suite passes", () => {
  const python = findPython();
  const result = spawnSync(
    python.command,
    [...python.prefix, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_query_gateway.py", "-v"],
    // Inherit the HTTP access log on Windows; piping it can backpressure the
    // child server during the Origin rejection test and abort the next request.
    { cwd: repo, stdio: "inherit", timeout: 60000 },
  );
  assert.equal(result.status, 0);
});
