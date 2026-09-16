import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

test("public event fusion runtime suite passes", () => {
  const result = spawnSync(
    "python",
    ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_public_event_fusion.py", "-v"],
    { cwd: repo, encoding: "utf8" },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 9 tests/);
  assert.match(result.stderr, /OK/);
});

test("public event fusion CLI self-check merges three sources and adds bounded background", () => {
  const result = spawnSync("python", ["-X", "utf8", "scripts/public-event-fusion.py", "--self-check"], {
    cwd: repo,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /PUBLIC_EVENT_FUSION_SELF_CHECK_OK/);
  assert.match(result.stdout, /sources=3/);
});
