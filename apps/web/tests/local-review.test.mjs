import assert from "node:assert/strict";
import test from "node:test";

import {
  addLocalReviewFeedback,
  emptyLocalReview,
  exportLocalReview,
  loadLocalReview,
  projectLocalReview,
  saveLocalReview,
  setLocalReviewDecision,
  syncLocalReview,
  validateLocalReview,
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

function gatewayItem(type, id) {
  const hash = "c".repeat(64);
  return {
    review_id: `${type}-${id}`,
    reason: "WRONG_CHANGE_CLASSIFICATION",
    status: "OPEN",
    source_version: "query-generation-1",
    evidence_sha256: hash,
    original_output_sha256: hash,
    feedback_target: { type, id, version: "query-generation-1" },
    entity_ids: { query_id: id, output_sha256: hash },
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

test("review feedback drafts are deduplicated and stay bound to the reviewed event version", () => {
  const state = syncLocalReview(emptyLocalReview(), [item()], T0);
  const first = addLocalReviewFeedback(state, item(), "FALSE_MERGE", T1);
  const same = addLocalReviewFeedback(first, item(), "FALSE_MERGE", T1);
  const drafts = Object.values(same.feedback);
  assert.equal(drafts.length, 1);
  assert.equal(drafts[0].target.type, "EVENT");
  assert.equal(drafts[0].source_binding.evidence_sha256, "a".repeat(64));
  assert.match(exportLocalReview(same, "markdown").content, /feedback drafts/);
});

test("gateway query and answer feedback drafts keep output hashes without a Review Inbox item", () => {
  const query = addLocalReviewFeedback(emptyLocalReview(), gatewayItem("QUERY", "query-1"), "NOT_RELEVANT", T1);
  const answer = addLocalReviewFeedback(query, gatewayItem("ANSWER", "answer-1"), "UNSUPPORTED_ANSWER", T1);
  const drafts = Object.values(answer.feedback);
  assert.deepEqual(drafts.map((draft) => draft.target.type).sort(), ["ANSWER", "QUERY"]);
  assert.ok(drafts.every((draft) => draft.original_output_sha256 === "c".repeat(64)));
  assert.match(exportLocalReview(answer, "markdown").content, /original_output_sha256/);
  assert.doesNotThrow(() => validateLocalReview(answer));
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

  const withFeedback = addLocalReviewFeedback(state, item(), "FALSE_MERGE", T1);
  const brokenFeedback = JSON.parse(JSON.stringify(withFeedback));
  const feedbackId = Object.keys(brokenFeedback.feedback)[0];
  brokenFeedback.feedback[feedbackId].binding_id = "tampered";
  assert.throws(() => validateLocalReview(brokenFeedback), /本機 feedback 無法驗證/);
  const missingBinding = JSON.parse(JSON.stringify(withFeedback));
  delete missingBinding.feedback[feedbackId].source_binding;
  assert.throws(() => validateLocalReview(missingBinding), /本機 feedback 無法驗證/);
});
