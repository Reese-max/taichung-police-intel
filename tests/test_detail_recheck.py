import unittest

from intel_v2.detail_recheck import classify_observation, plan_recheck
from intel_v2.detail_recheck_http import recheck_detail


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


class FakeHTTPResponse:
    def __init__(self, status_code=200, body=b"", *, url="https://official.test/detail", headers=None):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.body = body
        self.closed = False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]

    def close(self):
        self.closed = True


class FakeHTTPSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class DetailRecheckHTTPTests(unittest.TestCase):
    def test_baseline_extracts_hashes_attachments_and_conditional_metadata(self):
        first = FakeHTTPResponse(
            body=(
                b"<html><body>notice</body>"
                b"<a href='/files/a.pdf'>PDF</a>"
                b"<a href='https://evil.test/files/foreign.pdf'>foreign</a></html>"
            ),
            headers={"Content-Type": "text/html", "ETag": '"v1"'},
        )
        result = recheck_detail(
            FakeHTTPSession(first),
            "https://official.test/detail",
            None,
            observed_at=NOW,
            allowed_hosts={"official.test"},
        )
        self.assertEqual(result["classification"]["status"], "BASELINE")
        self.assertEqual(len(result["observation"]["attachments"]), 1)
        self.assertEqual(result["observation"]["etag"], '"v1"')
        self.assertEqual(result["request_headers"], {})

    def test_unapproved_attachment_host_is_not_treated_as_source_evidence(self):
        result = recheck_detail(
            FakeHTTPSession(FakeHTTPResponse(body=b"<a href='https://evil.test/files/a.pdf'>PDF</a>")),
            "https://official.test/detail",
            None,
            observed_at=NOW,
            allowed_hosts={"official.test"},
        )
        self.assertEqual(result["observation"]["attachments"], [])

    def test_body_is_available_only_when_explicitly_requested_for_snapshot_persistence(self):
        result = recheck_detail(
            FakeHTTPSession(FakeHTTPResponse(body=b"<p>notice</p>", headers={"Content-Type": "text/html"})),
            "https://official.test/detail",
            None,
            observed_at=NOW,
            allowed_hosts={"official.test"},
            include_body=True,
        )
        self.assertEqual(result["response_body"], b"<p>notice</p>")
        self.assertEqual(result["observation"]["content_type"], "text/html")

    def test_attachment_url_change_is_reviewable_without_downloading_attachment(self):
        session = FakeHTTPSession(
            FakeHTTPResponse(body=b"<a href='/files/a.pdf'>PDF</a>"),
            FakeHTTPResponse(body=b"<a href='/files/b.pdf'>PDF</a>"),
        )
        first = recheck_detail(
            session,
            "https://official.test/detail",
            None,
            observed_at=NOW,
            allowed_hosts={"official.test"},
        )
        second = recheck_detail(
            session,
            "https://official.test/detail",
            first["classification"]["after"],
            observed_at="2026-09-22T12:00:00+08:00",
            allowed_hosts={"official.test"},
        )
        self.assertEqual(second["classification"]["status"], "ATTACHMENT_CHANGED")
        self.assertTrue(second["classification"]["review_required"])

    def test_304_preserves_lkg_and_requires_attachment_recheck(self):
        session = FakeHTTPSession(FakeHTTPResponse(304, headers={"ETag": '"v1"'}))
        result = recheck_detail(
            session,
            "https://official.test/detail",
            BEFORE,
            observed_at=NOW,
            allowed_hosts={"official.test"},
        )
        self.assertEqual(result["classification"]["status"], "NOT_MODIFIED")
        self.assertTrue(result["classification"]["attachment_recheck_required"])
        self.assertEqual(session.calls[0][1]["headers"]["If-None-Match"], '"old"')

    def test_deferred_and_transport_bounds_fail_closed(self):
        deferred = recheck_detail(
            FakeHTTPSession(FakeHTTPResponse(429, headers={"Retry-After": "120"})),
            "https://official.test/detail",
            BEFORE,
            observed_at=NOW,
            allowed_hosts={"official.test"},
        )
        self.assertEqual(deferred["classification"]["status"], "DEFERRED")
        self.assertEqual(deferred["classification"]["retry_after_seconds"], 120)

        oversized = recheck_detail(
            FakeHTTPSession(FakeHTTPResponse(body=b"12345")),
            "https://official.test/detail",
            None,
            observed_at=NOW,
            allowed_hosts={"official.test"},
            max_body_bytes=4,
        )
        self.assertEqual(oversized["classification"]["status"], "UNAVAILABLE")
        self.assertEqual(oversized["transport_error"]["type"], "ValueError")

    def test_unapproved_redirect_is_not_followed(self):
        session = FakeHTTPSession(
            FakeHTTPResponse(
                302,
                headers={"Location": "https://evil.test/detail"},
            )
        )
        result = recheck_detail(
            session,
            "https://official.test/detail",
            None,
            observed_at=NOW,
            allowed_hosts={"official.test"},
        )
        self.assertEqual(result["classification"]["status"], "UNAVAILABLE")
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
