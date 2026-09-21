import unittest

from intel_v2.detail_recheck import classify_observation, plan_recheck


NOW = "2026-09-21T12:00:00+08:00"
BEFORE = {
    "document_version_id": "DOCV-OLD",
    "body_sha256": "a" * 64,
    "normalized_text_sha256": "b" * 64,
    "attachments": [{"attachment_id": "pdf-1", "content_sha256": "c" * 64}],
    "last_checked_at": "2026-09-20T12:00:00+08:00",
    "etag": '"old"',
    "last_modified": "Wed, 20 Sep 2026 04:00:00 GMT",
    "expires_at": "2026-09-22T12:00:00+08:00",
}


class DetailRecheckTests(unittest.TestCase):
    def test_due_plan_preserves_conditional_request_headers(self):
        result = plan_recheck(BEFORE, NOW, interval_hours=24)
        self.assertEqual(result["status"], "DUE")
        self.assertEqual(result["reason"], "TTL_EXPIRED")
        self.assertEqual(result["request_headers"]["If-None-Match"], '"old"')
        self.assertEqual(result["request_headers"]["If-Modified-Since"], BEFORE["last_modified"])

    def test_not_due_plan_does_not_request_again(self):
        result = plan_recheck(BEFORE, "2026-09-21T11:59:59+08:00", interval_hours=24)
        self.assertEqual(result["status"], "NOT_DUE")
        self.assertEqual(result["request_headers"], {})

    def test_first_observation_is_baseline_without_new_change(self):
        result = classify_observation(
            None,
            {"body_sha256": "a" * 64, "normalized_text_sha256": "b" * 64, "attachments": []},
            observed_at=NOW,
        )
        self.assertEqual(result["status"], "BASELINE")
        self.assertFalse(result["review_required"])
        self.assertTrue(result["baseline"])

    def test_normalized_text_change_marks_old_evidence_for_review(self):
        result = classify_observation(
            BEFORE,
            {
                "body_sha256": "d" * 64,
                "normalized_text_sha256": "e" * 64,
                "attachments": BEFORE["attachments"],
                "effective_at": "2026-09-21T16:00:00+08:00",
            },
            observed_at=NOW,
        )
        self.assertEqual(result["status"], "MATERIAL_CHANGE")
        self.assertTrue(result["review_required"])
        self.assertIn("effective_at", result["changed_fields"])
        self.assertEqual(result["before"]["document_version_id"], "DOCV-OLD")

    def test_raw_only_change_is_kept_as_non_material_version(self):
        result = classify_observation(
            BEFORE,
            {
                "body_sha256": "d" * 64,
                "normalized_text_sha256": BEFORE["normalized_text_sha256"],
                "attachments": BEFORE["attachments"],
            },
            observed_at=NOW,
        )
        self.assertEqual(result["status"], "PRESENTATION_ONLY")
        self.assertFalse(result["review_required"])
        self.assertEqual(result["after"]["etag"], BEFORE["etag"])

    def test_304_never_claims_attachments_unchanged(self):
        result = classify_observation(
            BEFORE,
            {"status_code": 304, "attachments_checked": False},
            observed_at=NOW,
        )
        self.assertEqual(result["status"], "NOT_MODIFIED")
        self.assertTrue(result["attachment_recheck_required"])
        self.assertFalse(result["review_required"])

    def test_failure_preserves_last_known_good_and_does_not_cancel_event(self):
        result = classify_observation(
            BEFORE,
            {"status_code": 503},
            observed_at=NOW,
        )
        self.assertEqual(result["status"], "DEFERRED")
        self.assertTrue(result["preserve_last_known_good"])
        self.assertFalse(result["event_cancelled"])


if __name__ == "__main__":
    unittest.main()
