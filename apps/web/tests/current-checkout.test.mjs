import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const component = path.resolve(here, "../components/V2DailyDashboard.js");
const skipDirtyFixtureRuntime = process.env.GOVINTEL_SKIP_DIRTY_FIXTURE_RUNTIME === "1";
const skipPublicationRuntime = process.env.GOVINTEL_PUBLICATION_WORKFLOW === "1";

function python() {
  for (const candidate of process.platform === "win32" ? ["python", "py"] : ["python3", "python"]) {
    const result = spawnSync(candidate, ["--version"], { stdio: "ignore" });
    if (result.status === 0) return candidate;
  }
  throw new Error("Python is required for the current-checkout bridge");
}

test("current-checkout runtime contract passes", {
  skip: skipDirtyFixtureRuntime
    ? "fixture regression intentionally dirties tracked publication files"
    : skipPublicationRuntime
      ? "Pages publication workflow intentionally materializes tracked publication files"
      : false,
}, () => {
  const result = spawnSync(python(), ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_current_checkout_e2e.py", "-v"], {
    cwd: repo,
    encoding: "utf8",
    timeout: 300000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 30 tests/);
  assert.match(result.stderr, /OK/);
});

test("dashboard refuses mixed publication generations", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /samePublicationGeneration/);
  assert.match(source, /generationMixed/);
  assert.match(source, /generation-mixed/);
  assert.match(source, /拒絕混版呈現/);
});

test("dashboard exposes candidate provenance and unavailable capabilities", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /candidate\.json/);
  assert.match(source, /GOVINTEL_CANDIDATE_MANIFEST/);
  assert.match(source, /query-generation/);
  assert.match(source, /unavailable_capabilities/);
  assert.match(source, /保存快照 · 非即時資料/);
});
