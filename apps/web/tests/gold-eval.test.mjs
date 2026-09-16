import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

test("gold evaluation unit suite passes", () => {
  const result = spawnSync(
    "python",
    ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_gold_evaluator.py", "-v"],
    { cwd: repo, encoding: "utf8" },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 8 tests/);
  assert.match(result.stderr, /OK/);
});

test("gold evaluation CLI self-check detects a deliberate false merge", () => {
  const result = spawnSync("python", ["-X", "utf8", "scripts/evaluate-govintel.py", "--self-check"], {
    cwd: repo,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /GOLD_EVAL_SELF_CHECK_OK/);
});
