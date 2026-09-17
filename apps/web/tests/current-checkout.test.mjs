import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const componentUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);

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
  return spawnSync(python.command, [...python.prefix, ...args], { cwd: repo, encoding: "utf8", timeout: 300000 });
}

test("current-checkout e2e runtime suite passes", () => {
  const result = runPython(["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_current_checkout_e2e.py", "-v"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 22 tests/);
  assert.match(result.stderr, /OK/);
});

test("dashboard exposes generation identity and refuses mixed generations", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /samePublicationGeneration/);
  assert.match(source, /generationMixed/);
  assert.match(source, /拒絕合併呈現/);
  assert.match(source, /data-testid="generation-mixed"/);
  assert.match(source, /data-testid="candidate-version"/);
  assert.match(source, /source_collection_run_id/);
});

test("dashboard surfaces candidate query index, policy and unavailable capabilities", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /candidate\.json/);
  assert.match(source, /GOVINTEL_CANDIDATE_MANIFEST/);
  assert.match(source, /query_generation_id/);
  assert.match(source, /policy_version/);
  assert.match(source, /unavailable_capabilities/);
  assert.match(source, /\{row\.status\}/);
  assert.match(source, /保存快照 · 非即時資料/);
});
