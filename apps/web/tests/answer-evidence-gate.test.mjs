import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

test("answer evidence gate runtime suite passes", () => {
  const result = spawnSync(
    "python",
    ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_answer_evidence_gate.py", "-v"],
    { cwd: repo, encoding: "utf8" },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 10 tests/);
  assert.match(result.stderr, /OK/);
});

test("answer evidence CLI self-check rejects unsupported cause while preserving supported time", () => {
  const result = spawnSync("python", ["-X", "utf8", "scripts/answer-evidence-gate.py", "--self-check"], {
    cwd: repo,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /ANSWER_EVIDENCE_GATE_SELF_CHECK_OK/);
  assert.match(result.stdout, /NEEDS_QUALIFICATION/);
});
