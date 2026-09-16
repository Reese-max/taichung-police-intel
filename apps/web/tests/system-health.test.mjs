import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

test("system-health runtime suite passes", () => {
  const result = spawnSync(
    "python",
    ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_system_health.py", "-v"],
    { cwd: repo, encoding: "utf8" },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 8 tests/);
  assert.match(result.stderr, /OK/);
});

test("system-health CLI self-check keeps query failure separate from publication failure", () => {
  const result = spawnSync("python", ["-X", "utf8", "scripts/system-health.py", "--self-check"], {
    cwd: repo,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /SYSTEM_HEALTH_SELF_CHECK_OK/);
  assert.match(result.stdout, /query_failure_preserves_publication=true/);
  assert.match(result.stdout, /publish_failure_blocks=true/);
});
