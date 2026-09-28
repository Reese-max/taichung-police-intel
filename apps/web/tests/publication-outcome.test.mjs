import assert from "node:assert/strict";
import test from "node:test";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Included by the existing web tests/*.test.mjs gate; no separate CI workflow.
test("publication outcome regressions run through the existing web gate", () => {
  const run = spawnSync(process.platform === "win32" ? "python" : "python3",
    ["-m", "unittest", "discover", "-s", "tests", "-p", "test_publication_outcome.py", "-v"],
    {cwd: fileURLToPath(new URL("../../../", import.meta.url)), encoding: "utf8", timeout: 30_000});
  assert.ifError(run.error);
  assert.equal(run.status, 0, `${run.stdout}\n${run.stderr}`);
});
