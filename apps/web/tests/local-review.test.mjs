import assert from "node:assert/strict";
import test from "node:test";

import {
  emptyLocalReview,
  exportLocalReview,
  loadLocalReview,
  projectLocalReview,
  saveLocalReview,
  setLocalReviewDecision,
  syncLocalReview,
} from "../lib/local-review.js";

const T0 = "2026-09-21T00:00:00+00:00";
const T1 = "2026-09-22T00:00:00+00:00";

function item(version = 1, hash = "a".repeat(64)) {
  return {
    review_id: "REVIEW-S-007",
    reason: "CONFLICT",
    status: "OPEN",
    source_version: version,
    evidence_sha256: hash,
    entity_ids: { source_id: "S-007", event_id: "E-1" },
  };
}

test("local review is deduplicated and a new evidence binding reopens it", () => {
  const first = syncLocalReview(emptyLocalReview(), [item()], T0);
  const same = syncLocalReview(first, [item()], T1);
  assert.equal(same.items["REVIEW-S-007"].history.length, 1);

  const resolved = setLocalReviewDecision(same, "REVIEW-S-007", "RESOLVED", T0);
  assert.equal(resolved.items["REVIEW-S-007"].local_status, "RESOLVED");
  const reopened = syncLocalReview(resolved, [item(2, "b".repeat(64))], T1);
  assert.equal(reopened.items["REVIEW-S-007"].local_status, "OPEN");
  assert.equal(reopened.items["REVIEW-S-007"].history.at(-1).action, "REOPENED");
  assert.equal(projectLocalReview(reopened, [item(2, "b".repeat(64))])[0].local_status, "OPEN");
});

test("local decisions keep the source binding and export an explicit local-only receipt", () => {
  const state = syncLocalReview(emptyLocalReview(), [item()], T0);
  const decided = setLocalReviewDecision(state, "REVIEW-S-007", "KEEP_WATCHING", T1);
  const stored = decided.items["REVIEW-S-007"];
  assert.equal(stored.decision.binding_id, stored.binding_id);
  assert.match(exportLocalReview(decided, "markdown").content, /僅保存於本機瀏覽器/);
  assert.match(exportLocalReview(decided, "json").content, /KEEP_WATCHING/);
});

test("local review storage round-trips and rejects an altered binding", () => {
  const storage = {
    value: null,
    getItem() { return this.value; },
    setItem(_key, value) { this.value = value; },
  };
  const state = syncLocalReview(emptyLocalReview(), [item()], T0);
  saveLocalReview(state, storage);
  assert.deepEqual(loadLocalReview(storage), state);
  const tampered = JSON.parse(storage.value);
  tampered.items["REVIEW-S-007"].evidence_sha256 = "c".repeat(64);
  assert.throws(() => loadLocalReview({ getItem: () => JSON.stringify(tampered) }), /本機覆核資料無法驗證/);
});
