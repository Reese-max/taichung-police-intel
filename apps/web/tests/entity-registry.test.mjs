import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
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

test("entity registry runtime suite passes", () => {
  const result = runPython(["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_entity_registry.py", "-v"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 17 tests/);
  assert.match(result.stderr, /OK/);
});

test("entity registry CLI self-check resolves aliases without fuzzy promotion", () => {
  const result = runPython(["-X", "utf8", "scripts/entity-registry.py", "--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /ENTITY_REGISTRY_SELF_CHECK_OK/);
});
