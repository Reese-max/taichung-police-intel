import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../", import.meta.url));
const command = process.platform === "win32" ? "python" : "python3";

function retentionSelfCheck(args) {
  return spawnSync(command, ["-X", "utf8", "scripts/retention-policy.py", ...args], {
    cwd: repo,
    encoding: "utf8",
    timeout: 60000,
  });
}

test("retention policy self-check is executable", () => {
  const result = retentionSelfCheck(["--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /RETENTION_POLICY_SELF_CHECK_OK/);
});

test("retention receipt never advertises full text or an unbounded public surface", () => {
  const result = retentionSelfCheck(["--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /full_text_allowed=false/);
  assert.match(result.stdout, /dry_run=true/);
  assert.match(result.stdout, /audit_preserved=true/);
  assert.match(result.stdout, /version=2 /);
});

test("compiled retention policy binds a versioned matrix, terms and archive eligibility", () => {
  const result = retentionSelfCheck(["--json"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const policy = JSON.parse(result.stdout);
  assert.equal(policy.policy_version, 2);
  assert.match(policy.policy_hash, /^[0-9a-f]{64}$/);
  assert.equal(Object.keys(policy.data_types).length, 10);
  assert.deepEqual(Object.keys(policy.layer_policies).sort(), [
    "canonical_event",
    "normalized_document",
    "publication",
    "query_index",
    "raw_snapshot",
  ]);
  for (const [sourceId, source] of Object.entries(policy.source_policies)) {
    assert.match(source.terms_url, /^https:\/\//, sourceId);
    assert.ok(source.review_required, sourceId);
    if (source.rights_status === "UNKNOWN") {
      assert.equal(source.terms_status, "CATALOG_ENTRYPOINT_NOT_A_LICENSE", sourceId);
    }
  }
});