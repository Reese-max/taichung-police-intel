import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

test("canonical query store rebuild/query runtime suite passes", () => {
  const result = spawnSync(
    "python",
    ["-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-p", "test_query_store.py", "-v"],
    { cwd: repo, encoding: "utf8" },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stderr, /Ran 7 tests/);
  assert.match(result.stderr, /OK/);
});

test("query-store CLI self-check uses the checked-in canonical publication", () => {
  const result = spawnSync("python", ["-X", "utf8", "scripts/query-store.py", "self-check"], {
    cwd: repo,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /QUERY_STORE_SELF_CHECK_OK/);
});
