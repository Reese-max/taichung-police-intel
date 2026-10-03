import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../", import.meta.url));
const command = process.platform === "win32" ? "python" : "python3";

function retention(args) {
  return spawnSync(command, ["-X", "utf8", "scripts/retention-policy.py", ...args], {
    cwd: repo,
    encoding: "utf8",
    timeout: 60000,
  });
}

test("retention policy self-check is executable", () => {
  const result = retention(["--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /RETENTION_POLICY_SELF_CHECK_OK/);
});

test("self-check receipt reports facts derived from the compiled policy", () => {
  const compiled = JSON.parse(retention(["--json"]).stdout);
  const binding = JSON.parse(retention(["--binding"]).stdout);
  const result = retention(["--self-check"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  // Asserted against the compiled policy, never against a hardcoded literal, so the
  // receipt cannot claim something the policy does not enforce.
  assert.match(result.stdout, new RegExp(`version=${compiled.policy_version} `));
  assert.match(result.stdout, new RegExp(`policy_hash=${compiled.policy_hash.slice(0, 12)}`));
  assert.match(result.stdout, new RegExp(`full_text_allowed=${binding.full_text_allowed}`));
  assert.match(result.stdout, new RegExp(`rights_status=${binding.rights_status}`));
  assert.match(result.stdout, new RegExp(`terms_status=${binding.terms_status}`));
  assert.equal(binding.full_text_allowed, false);
  assert.equal(binding.rights_status, "UNKNOWN");
  assert.match(result.stdout, /purge_executed=false dry_run=true/);
});

test("compiled policy binds a versioned matrix, terms and archive eligibility", () => {
  const result = retention(["--json"]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const policy = JSON.parse(result.stdout);
  assert.match(policy.policy_hash, /^[0-9a-f]{64}$/);
  assert.deepEqual(Object.keys(policy.layer_policies).sort(), [
    "canonical_event",
    "normalized_document",
    "publication",
    "query_index",
    "raw_snapshot",
  ]);
  const contentTypes = Object.values(policy.data_types).filter((entry) => entry.kind === "CONTENT");
  assert.equal(contentTypes.length, 5);
  for (const [sourceId, source] of Object.entries(policy.source_policies)) {
    assert.match(source.terms_url, /^https:\/\//, sourceId);
    assert.ok(source.review_required, sourceId);
    assert.ok(source.data_types.length > 0, sourceId);
    if (source.rights_status === "UNKNOWN") {
      assert.equal(source.terms_status, "CATALOG_ENTRYPOINT_NOT_A_LICENSE", sourceId);
    }
  }
  assert.deepEqual(policy.layer_policies.raw_snapshot, {
    archive_eligibility: "NOT_ARCHIVABLE",
    public_projection: "NONE",
    retention_class_source: "raw_retention_class",
  });
  assert.equal(policy.layer_policies.query_index.public_projection, "METADATA_LINK_ONLY");
});

test("checked-in binding cannot drift from the compiled policy", () => {
  const committed = JSON.parse(
    readFileSync(new URL("../public/data/retention-policy-binding.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(committed, JSON.parse(retention(["--binding"]).stdout));
});

test("deployed worker gateway serves the generated binding, not a hand-written one", () => {
  const worker = readFileSync(
    new URL("../../../workers/query-gateway/src/index.js", import.meta.url), "utf8",
  );
  assert.match(worker, /retention-policy-binding\.json/);
  // No hand-written retention literal and no pinned retention hash may survive.
  assert.doesNotMatch(worker, /RETENTION_POLICY = \{/);
  const retentionPolicy = JSON.parse(retention(["--json"]).stdout);
  assert.equal(worker.includes(retentionPolicy.policy_hash), false);
  // The protocol version must stay pinned; verify-query-gateway-production.py checks it.
  assert.match(worker, /MCP_PROTOCOL_VERSION = "2025-06-18"/);
});

test("expiry dry-run and query-index projection run end to end", () => {
  const dir = mkdtempSync(join(tmpdir(), "retention-cli-"));
  const manifest = join(dir, "manifest.json");
  const receiptPath = join(dir, "receipt.json");
  writeFileSync(manifest, JSON.stringify({
    records: [
      {
        record_id: "news-1",
        source_id: "S-001",
        layer: "canonical_event",
        captured_at: "2026-01-01T00:00:00+00:00",
        content_sha256: "a".repeat(64),
        audit_refs: ["AUDIT-1"],
      },
      {
        record_id: "news-raw",
        source_id: "S-001",
        layer: "raw_snapshot",
        captured_at: "2026-01-01T00:00:00+00:00",
        content_sha256: "b".repeat(64),
        audit_refs: ["AUDIT-2"],
      },
    ],
  }));
  const plan = spawnSync(command, ["-X", "utf8", "scripts/retention-policy.py", "--plan", manifest,
    "--at", "2026-09-21T00:00:00+00:00", "--output", receiptPath], {
    cwd: repo,
    encoding: "utf8",
    timeout: 60000,
  });
  assert.equal(plan.status, 0, `${plan.stdout}\n${plan.stderr}`);
  assert.match(plan.stdout, /RETENTION_DRY_RUN_OK/);
  const receipt = JSON.parse(readFileSync(receiptPath, "utf8"));
  assert.equal(receipt.dry_run, true);
  assert.equal(receipt.counts.total, 2);
  assert.equal(receipt.receipt_sha256.length, 64);

  // The rebuild only accepts query_index records; the raw snapshot must be refused
  // rather than projected, and the exit code must be non-zero.
  const index = spawnSync(command, ["-X", "utf8", "scripts/retention-policy.py", "--project", manifest,
    "--at", "2026-09-21T00:00:00+00:00"], {
    cwd: repo,
    encoding: "utf8",
    timeout: 60000,
  });
  assert.equal(index.status, 1);
  assert.match(index.stderr, /unsupported retention layer for the query index: canonical_event/);
  rmSync(dir, { recursive: true, force: true });
});
