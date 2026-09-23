import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildHealthResponse } from "../lib/health-response.mjs";
import { PUBLICATION_POLICY_BINDING } from "../lib/publication-freshness.mjs";

const status = JSON.parse(await readFile(new URL("../public/data/source-status.json", import.meta.url)));
const brief = JSON.parse(await readFile(new URL("../public/data/v2-daily-brief.json", import.meta.url)));
const publicationWorkflow = process.env.GOVINTEL_PUBLICATION_WORKFLOW === "1";

test("health endpoint does not call a stale checked-in snapshot ok", {
  skip: publicationWorkflow
    ? "Pages publication uses generated source-status; publication validators cover its freshness"
    : false,
}, () => {
  const response = buildHealthResponse(status, brief, Date.parse("2026-09-21T12:00:00+08:00"));
  assert.equal(response.status, "stale");
  assert.equal(response.health, "STALE");
  assert.equal(response.can_reassure, false);
  assert.equal(response.stale_sources, 3);
  assert.equal(response.deployment_verified, false);
  assert.equal(response.public_http_verified, false);
  assert.deepEqual(response.policy, PUBLICATION_POLICY_BINDING);
  assert.match(response.policy.policy_hash, /^[0-9a-f]{64}$/);
  assert.match(response.policy.catalog_hash, /^[0-9a-f]{64}$/);
});

test("static export health never freezes a build-time ok status", () => {
  const response = buildHealthResponse(status, brief);
  assert.equal(response.status, "unknown");
  assert.equal(response.health, "UNKNOWN");
  assert.equal(response.can_reassure, false);
  assert.equal(response.snapshot_age_ms, null);
  assert.match(response.reason, /靜態輸出/);
});

test("health response stays explicit when source state is incomplete", () => {
  const incomplete = { ...status, sources: [] };
  const response = buildHealthResponse(incomplete, brief, Date.parse("2026-09-11T09:00:00+08:00"));
  assert.equal(response.status, "unknown");
  assert.equal(response.health, "UNKNOWN");
  assert.equal(response.can_reassure, false);
});

test("invalid mode fails closed", () => {
  assert.throws(
    () => buildHealthResponse({ ...status, mode: "UNKNOWN" }, brief, Date.now()),
    /invalid demo state/,
  );
});
