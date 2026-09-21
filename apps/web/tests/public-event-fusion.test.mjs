import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

function findPython() {
  const candidates = process.platform === "win32"
    ? [{ command: "python", prefix: [] }, { command: "py", prefix: ["-3"] }]
    : [{ command: "python3", prefix: [] }, { command: "python", prefix: [] }];
  for (const candidate of candidates) {
    const result = spawnSync(candidate.command, [...candidate.prefix, "--version"], { stdio: "ignore" });
    if (result.status === 0) return candidate;
  }
  throw new Error("No supported Python interpreter found");
}

const python = findPython();
function runPython(args) {
  return spawnSync(python.command, [...python.prefix, ...args], { cwd: repo, encoding: "utf8" });
}

test("public event fusion regression suite passes", () => {
  const result = runPython(["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_public_event_fusion.py", "-v"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 15 tests/);
  assert.match(result.stderr, /OK/);
});

test("public event fusion CLI replays official sources, revision, and background guard", () => {
  const result = runPython(["-X", "utf8", "scripts/public-event-fusion.py", "--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /PUBLIC_EVENT_FUSION_SELF_CHECK_OK/);
  assert.match(result.stdout, /sources=3/);
  assert.match(result.stdout, /revision=conflict/);
});
