from __future__ import annotations

import copy
import json
import unittest

from intel_v2.review import claim, decide, detail_recheck_candidates, empty_state, project, reconcile, schema_drift_candidates, upsert, validate_state


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

    def test_detail_recheck_maps_material_change_to_review_candidate(self):
        outcomes = [{
            "source_id": "S-004",
            "stable_key": "agenda-1",
            "classification": {
                "status": "MATERIAL_CHANGE",
                "review_required": True,
                "changed_fields": ["effective_at"],
                "before": {"document_version_id": "DOCV-OLD", "normalized_text_sha256": "a" * 64},
                "after": {"document_version_id": "DOCV-NEW", "normalized_text_sha256": "b" * 64},
            },
        }]
        mapped = detail_recheck_candidates(outcomes)
        self.assertEqual(mapped[0]["reason"], "NEEDS_REVIEW")
        self.assertEqual(mapped[0]["entity_ids"]["document_id"], "DOCV-NEW")
        state = reconcile(empty_state(), mapped, observed_at=STAMP)
        self.assertEqual(project(state)[0]["source_version"], "DOCV-NEW")

    def test_detail_recheck_ignores_unchanged_outcome(self):
        self.assertEqual(detail_recheck_candidates([{
            "source_id": "S-004",
            "stable_key": "agenda-1",
            "classification": {"status": "UNCHANGED", "review_required": False},
        }]), [])

    def test_tampered_audit_and_evidence_receipts_fail_closed(self):
        state, item, _ = upsert(empty_state(), candidate("CONFLICT"), observed_at=STAMP)
        broken_audit = copy.deepcopy(state)
        broken_audit["items"][item["review_id"]]["audit"][0]["payload"]["reason"] = "STALE_SOURCE"
        with self.assertRaisesRegex(ValueError, "audit receipt hash mismatch"):
            validate_state(broken_audit)

        broken_evidence = copy.deepcopy(state)
        broken_evidence["items"][item["review_id"]]["evidence"]["after"]["version"] = 99
        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            validate_state(broken_evidence)

        with self.assertRaisesRegex(ValueError, "review state must be an object"):
            validate_state([])

    def test_public_projection_keeps_bounded_ids_but_strips_private_review_data(self):
        state, item, _ = upsert(empty_state(), candidate("CONFLICT"), observed_at=STAMP)
        state = claim(state, item["review_id"], assignee_ref="operator-1", claimed_at=STAMP)
        state = decide(
            state,
            item["review_id"],
            "merge",
            reviewer_ref="operator-1",
            decided_at=STAMP,
            evidence={"event_ids": ["E-1", "E-2"], "private_notes": "do not publish"},
        )
        public = project(state, include_closed=True, public=True)
        self.assertEqual(public[0]["entity_ids"], {"source_id": "S-001", "event_id": "E-1"})
        self.assertNotIn("assignment", public[0])
        self.assertNotIn("decision", public[0])
        self.assertNotIn("audit", public[0])
        self.assertNotIn("private_notes", json.dumps(public, ensure_ascii=False))
        self.assertEqual(state["items"][item["review_id"]]["decision"]["version_receipt"]["source_version"], 1)


if __name__ == "__main__":
    unittest.main()
