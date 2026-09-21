from __future__ import annotations

import unittest

from intel_v2.review import claim, decide, empty_state, project, reconcile, schema_drift_candidates, upsert


STAMP = "2026-09-21T00:00:00+00:00"


def candidate(reason="NEEDS_REVIEW", fingerprint="source:S-001", version=1):
    return {
        "reason": reason,
        "fingerprint": fingerprint,
        "entity_ids": {"source_id": "S-001", "event_id": "E-1"},
        "source_version": version,
        "evidence": {"before": {"version": version - 1}, "after": {"version": version}},
    }


class ReviewInboxTests(unittest.TestCase):
    def test_same_fingerprint_is_idempotent(self):
        state = reconcile(empty_state(), [candidate()], observed_at=STAMP)
        state = reconcile(state, [candidate()], observed_at=STAMP)
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(len(next(iter(state["items"].values()))["audit"]), 1)

    def test_claim_and_decision_keep_audit_and_evidence(self):
        state, item, _ = upsert(empty_state(), candidate("CONFLICT"), observed_at=STAMP)
        state = claim(state, item["review_id"], assignee_ref="operator-1", claimed_at=STAMP)
        state = decide(state, item["review_id"], "merge", reviewer_ref="operator-1", decided_at=STAMP, evidence={"event_ids": ["E-1", "E-2"]})
        stored = state["items"][item["review_id"]]
        self.assertEqual(stored["status"], "RESOLVED")
        self.assertEqual(stored["decision"]["decision"], "MERGE")
        self.assertEqual(len(stored["audit"]), 3)
        self.assertEqual(len(project(state)), 0)
        self.assertEqual(len(project(state, include_closed=True)), 1)

    def test_new_evidence_reopens_terminal_item_without_replacing_history(self):
        state, item, _ = upsert(empty_state(), candidate(), observed_at=STAMP)
        state = decide(state, item["review_id"], "resolve", reviewer_ref="operator-1", decided_at=STAMP)
        state = reconcile(state, [candidate(version=2)], observed_at=STAMP)
        stored = state["items"][item["review_id"]]
        self.assertEqual(stored["status"], "OPEN")
        self.assertEqual(len(stored["evidence_history"]), 1)
        self.assertEqual(stored["audit"][-1]["action"], "REOPENED")

    def test_schema_drift_maps_to_controlled_candidate_and_missing_source_does_not_close(self):
        receipt = {"generated_at": STAMP, "review_inbox": [{"source_id": "S-007", "status": "SOURCE_UNAVAILABLE", "reasons": ["HTTP_503"], "observed_at": STAMP}]}
        mapped = schema_drift_candidates(receipt)
        self.assertEqual(mapped[0]["reason"], "PARTIAL_SOURCE")
        state = reconcile(empty_state(), mapped, observed_at=STAMP)
        state = reconcile(state, [], observed_at=STAMP)
        self.assertEqual(project(state)[0]["status"], "OPEN")


if __name__ == "__main__":
    unittest.main()
