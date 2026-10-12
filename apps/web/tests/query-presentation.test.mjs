import assert from "node:assert/strict";
import test from "node:test";
import { indexQueryPresentation } from "../lib/query-presentation.js";

test("schema-1 admission refusal is unavailable, never a genuine no-match", () => {
  const response = { results: [], result_count: 0, answerable_no_match: false,
    query_coverage: { status: "UNKNOWN", rights_blocked_sources: ["S-003"],
      formal_admission: { status: "UNKNOWN", reason: "APPROVED_GOVERNANCE_MISSING", blocked_sources: ["S-003"] },
      can_state_bounded_no_match: false } };
  const before = structuredClone(response);
  assert.equal(indexQueryPresentation(response).kind, "unavailable");
  assert.match(indexQueryPresentation(response).message, /metadata.*准入/);
  assert.deepEqual(response, before);
});

test("missing, stale and unsupported index coverage cannot establish a no-match", () => {
  for (const status of ["UNKNOWN", "RIGHTS_BLOCKED", "PARTIAL", "STALE", "CAPABILITY_NOT_AVAILABLE"]) {
    assert.equal(indexQueryPresentation({ results: [], answerable_no_match: false,
      query_coverage: { status, can_state_bounded_no_match: false } }).kind, "unavailable");
  }
  assert.equal(indexQueryPresentation({ results: [] }).kind, "unavailable");
});

test("a bounded empty result needs both server no-match assertions", () => {
  const response = { results: [], answerable_no_match: true,
    query_coverage: { status: "COVERED_BOUNDED_SCOPE", can_state_bounded_no_match: true } };
  assert.equal(indexQueryPresentation(response).kind, "empty");
  assert.equal(indexQueryPresentation({ ...response, answerable_no_match: false }).kind, "unavailable");
  assert.equal(indexQueryPresentation({ ...response, query_coverage: { status: "COVERED_BOUNDED_SCOPE" } }).kind, "unavailable");
});

test("partially admitted metadata remains visible with its coverage limitation", () => {
  const response = { results: [{ canonical_id: "ADMITTED-METADATA" }],
    query_coverage: { status: "RIGHTS_BLOCKED", rights_blocked_sources: ["BLOCKED-SOURCE"] } };
  assert.equal(indexQueryPresentation(response).kind, "matches");
  assert.match(indexQueryPresentation(response).message, /尚不完整/);
  assert.equal(response.results.length, 1);
});
