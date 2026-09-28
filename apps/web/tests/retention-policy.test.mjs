import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../", import.meta.url));

test("retention policy self-check is executable", () => {
  const command = process.platform === "win32" ? "python" : "python3";
  const result = spawnSync(command, ["-X", "utf8", "scripts/retention-policy.py", "--self-check"], {
    cwd: repo,
    encoding: "utf8",
    timeout: 60000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /RETENTION_POLICY_SELF_CHECK_OK/);
});
