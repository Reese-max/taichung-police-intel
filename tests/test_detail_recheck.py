import unittest
from datetime import datetime, timedelta

from intel_v2.detail_recheck import classify_observation, plan_recheck
from intel_v2.detail_recheck_budget import (
    DEFAULT_RECHECK_BUDGET_POLICY,
    acknowledge_recheck_budget,
    empty_budget_state,
    pending_retry_rows,
    plan_recheck_budget,
    record_recheck_budget,
)
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


class DetailRecheckBudgetTests(unittest.TestCase):
    """Per-source / per-host request budget, bounded backoff and pending retry state."""

    def target(self, stable_key, *, source_id="S-004", host="www.tccc.gov.tw", budget_state=None):
        return {
            "source_id": source_id,
            "stable_key": stable_key,
            "host": host,
            "budget_state": budget_state,
        }

    def state_with_attempts(self, *minutes_ago, **overrides):
        state = empty_budget_state()
        state["attempts"] = [
            (datetime.fromisoformat(NOW) - timedelta(minutes=offset)).isoformat(timespec="seconds")
            for offset in minutes_ago
        ]
        state.update(overrides)
        return state

    def test_first_request_is_allowed_and_records_exactly_one_attempt(self):
        state = empty_budget_state()
        decision = plan_recheck_budget([self.target("traffic-1", budget_state=state)], now=NOW)[0]
        self.assertEqual(decision["decision"], "ALLOW")
        self.assertEqual(decision["reason"], "ALLOWED")
        self.assertIsNone(decision["due_at"])
        self.assertEqual(decision["source_attempts_in_window"], 0)

        after = record_recheck_budget(state, {"status": "UNCHANGED"}, now=NOW)
        self.assertEqual(after["attempts"], [NOW])
        self.assertEqual(after["failure_streak"], 0)
        self.assertIsNone(after["deferred_until"])
        self.assertEqual(state["attempts"], [], "planning must not consume an attempt slot")

    def test_per_source_request_limit_defers_beyond_the_cap(self):
        targets = [
            self.target("a", budget_state=self.state_with_attempts(10)),
            self.target("b", budget_state=self.state_with_attempts(20)),
        ]
        capped = plan_recheck_budget(targets, now=NOW, policy={"per_source_limit": 2})
        self.assertEqual(
            [item["reason"] for item in capped],
            ["SOURCE_BUDGET_EXHAUSTED", "SOURCE_BUDGET_EXHAUSTED"],
        )
        self.assertEqual([item["decision"] for item in capped], ["DEFER", "DEFER"])
        self.assertEqual(capped[0]["source_attempts_in_window"], 2)
        self.assertEqual(
            capped[0]["due_at"],
            "2026-09-21T13:00:00+08:00",
            "a capped source must wait a full window instead of re-crawling immediately",
        )

        allowed = plan_recheck_budget(targets, now=NOW, policy={"per_source_limit": 3})
        self.assertEqual([item["decision"] for item in allowed], ["ALLOW", "DEFER"])
        self.assertEqual(allowed[0]["source_attempts_in_window"], 2)
        self.assertEqual(
            allowed[1]["reason"],
            "SOURCE_BUDGET_EXHAUSTED",
            "a granted request must consume the remaining slot for the rest of the run",
        )

    def test_per_host_limit_spans_sources_sharing_one_host(self):
        targets = [
            self.target("a", source_id="S-004", budget_state=self.state_with_attempts(10)),
            self.target("b", source_id="S-006", budget_state=self.state_with_attempts(120)),
        ]
        decisions = plan_recheck_budget(targets, now=NOW, policy={"per_host_limit": 2})
        self.assertEqual([item["reason"] for item in decisions], ["ALLOWED", "HOST_BUDGET_EXHAUSTED"])
        self.assertEqual(decisions[1]["host_attempts_in_window"], 2)

    def test_429_backs_off_exponentially_and_respects_the_source_retry_after(self):
        first = record_recheck_budget(
            empty_budget_state(), {"status": "DEFERRED", "retry_after_seconds": 120}, now=NOW
        )
        self.assertEqual(first["failure_streak"], 1)
        self.assertEqual(first["deferred_until"], "2026-09-21T12:15:00+08:00")
        self.assertEqual(first["deadline_at"], "2026-09-22T12:00:00+08:00")

        second = record_recheck_budget(
            first, {"status": "DEFERRED", "retry_after_seconds": 7200}, now="2026-09-21T12:15:00+08:00"
        )
        self.assertEqual(second["failure_streak"], 2)
        self.assertEqual(
            second["deferred_until"],
            "2026-09-21T14:15:00+08:00",
            "a source Retry-After longer than the local backoff must be honoured",
        )

        capped = record_recheck_budget(
            second, {"status": "DEFERRED", "retry_after_seconds": 90000}, now="2026-09-21T14:15:00+08:00"
        )
        self.assertEqual(capped["deferred_until"], "2026-09-22T14:15:00+08:00")
        self.assertEqual(
            capped["failure_streak"],
            3,
            "the local backoff must stay bounded even while the streak grows",
        )
        self.assertEqual(
            record_recheck_budget(
                capped, {"status": "DEFERRED", "retry_after_seconds": 90000},
                now="2026-09-21T14:15:00+08:00",
            )["deferred_until"],
            "2026-09-22T14:15:00+08:00",
        )

        local = record_recheck_budget(
            capped,
            {"status": "DEFERRED", "retry_after_seconds": 60},
            now="2026-09-21T14:15:00+08:00",
            policy={"max_backoff_seconds": 900},
        )
        self.assertEqual(
            local["deferred_until"],
            "2026-09-21T14:30:00+08:00",
            "a growing streak must stay inside the local backoff ceiling",
        )

    def test_active_backoff_defers_until_the_recorded_retry_time(self):
        state = self.state_with_attempts(5, deferred_until="2026-09-21T12:05:00+08:00", failure_streak=1)
        decision = plan_recheck_budget([self.target("a", budget_state=state)], now=NOW)[0]
        self.assertEqual(decision["decision"], "DEFER")
        self.assertEqual(decision["reason"], "BACKOFF_ACTIVE")
        self.assertEqual(decision["due_at"], "2026-09-21T12:05:00+08:00")

    def test_backoff_deadline_blocks_automatic_retry_until_acknowledged(self):
        state = self.state_with_attempts(
            30 * 24,
            failure_streak=6,
            first_deferred_at="2026-09-20T12:00:00+08:00",
            deadline_at="2026-09-21T12:00:00+08:00",
            deferred_until="2026-09-21T11:00:00+08:00",
        )
        decision = plan_recheck_budget([self.target("a", budget_state=state)], now=NOW)[0]
        self.assertEqual(decision["reason"], "RETRY_DEADLINE_EXCEEDED")
        self.assertEqual(decision["decision"], "DEFER")
        self.assertTrue(decision["retry_deadline_exceeded"])
        self.assertEqual(decision["due_at"], "2026-09-22T12:00:00+08:00")

        acknowledged = acknowledge_recheck_budget(state, now=NOW)
        self.assertFalse(acknowledged["retry_deadline_exceeded"])
        self.assertEqual(acknowledged["failure_streak"], 0)
        self.assertIsNone(acknowledged["deferred_until"])
        self.assertEqual(
            plan_recheck_budget([self.target("a", budget_state=acknowledged)], now=NOW)[0]["reason"],
            "ALLOWED",
        )

    def test_successful_check_clears_backoff_and_deadline(self):
        state = self.state_with_attempts(
            5,
            failure_streak=3,
            deferred_until="2026-09-21T12:30:00+08:00",
            first_deferred_at="2026-09-21T11:00:00+08:00",
            deadline_at="2026-09-22T11:00:00+08:00",
        )
        after = record_recheck_budget(state, {"status": "MATERIAL_CHANGE"}, now=NOW)
        self.assertEqual(after["failure_streak"], 0)
        self.assertIsNone(after["deferred_until"])
        self.assertIsNone(after["first_deferred_at"])
        self.assertIsNone(after["deadline_at"])
        self.assertFalse(after["retry_deadline_exceeded"])

    def test_unavailable_consumes_an_attempt_without_erasing_an_outstanding_backoff(self):
        state = self.state_with_attempts(
            10, 20, failure_streak=2, deferred_until="2026-09-21T11:00:00+08:00", deadline_at=NOW
        )
        after = record_recheck_budget(state, {"status": "UNAVAILABLE"}, now=NOW)
        self.assertEqual(
            after["failure_streak"],
            2,
            "an alternating 429/404 source must not be able to reset its own retry deadline",
        )
        self.assertEqual(after["deferred_until"], "2026-09-21T11:00:00+08:00")
        self.assertTrue(after["retry_deadline_exceeded"])
        self.assertEqual(after["last_decision"], "INCOMPLETE")
        self.assertEqual(len(after["attempts"]), 3)
        exhausted = plan_recheck_budget(
            [self.target("a", budget_state=self.state_with_attempts(10, 20))],
            now=NOW,
            policy={"per_source_limit": 2},
        )[0]
        self.assertEqual(exhausted["reason"], "SOURCE_BUDGET_EXHAUSTED")

    def test_run_limit_holds_back_targets_beyond_the_per_run_ceiling(self):
        targets = [self.target(f"a{index}") for index in range(3)]
        decisions = plan_recheck_budget(targets, now=NOW, run_limit=1)
        self.assertEqual(
            [item["reason"] for item in decisions],
            ["ALLOWED", "RUN_LIMIT_REACHED", "RUN_LIMIT_REACHED"],
        )
        self.assertIsNone(decisions[1]["due_at"], "a run-limit refusal must keep the existing due cursor")
        self.assertEqual(decisions[1]["budget_state"]["last_decision"], "RUN_LIMIT_REACHED")

    def test_duplicate_and_future_attempts_do_not_inflate_the_counter(self):
        state = self.state_with_attempts(10, 10, 10)
        state["attempts"].append("2026-09-21T18:00:00+08:00")
        decision = plan_recheck_budget(
            [self.target("a", budget_state=state)], now=NOW, policy={"per_source_limit": 1}
        )[0]
        self.assertEqual(decision["source_attempts_in_window"], 1)
        self.assertEqual(decision["reason"], "SOURCE_BUDGET_EXHAUSTED")

    def test_malformed_stored_ledger_is_refused_instead_of_raising(self):
        decisions = plan_recheck_budget(
            [
                self.target("a", budget_state={"attempts": "oops"}),
                self.target("b", budget_state=empty_budget_state()),
            ],
            now=NOW,
        )
        self.assertEqual([item["reason"] for item in decisions], ["INVALID_BUDGET_STATE", "ALLOWED"])
        self.assertEqual(
            decisions[0]["due_at"],
            "2026-09-21T13:00:00+08:00",
            "a poisoned ledger must be re-examined, never requested blind",
        )
        self.assertEqual(decisions[0]["budget_state"]["attempts"], [])
        stored = decisions[0]["budget_state"]
        self.assertEqual(
            stored["last_decision"],
            "INVALID_BUDGET_STATE",
            "the refusal must round-trip through the validator or the row stays unacknowledgeable",
        )
        self.assertEqual([row["stable_key"] for row in pending_retry_rows([self.target("a", budget_state=stored)], now=NOW)], ["a"])
        acknowledged = acknowledge_recheck_budget(stored, now=NOW)
        self.assertFalse(acknowledged["retry_deadline_exceeded"])
        self.assertEqual(
            plan_recheck_budget([self.target("a", budget_state=acknowledged)], now=NOW)[0]["reason"],
            "ALLOWED",
            "a repaired ledger must let the target back into the schedule",
        )

    def test_attempts_outside_the_window_are_neither_counted_nor_kept(self):
        state = self.state_with_attempts(120)
        decision = plan_recheck_budget([self.target("a", budget_state=state)], now=NOW)[0]
        self.assertEqual(decision["source_attempts_in_window"], 0)
        self.assertEqual(decision["reason"], "ALLOWED")
        self.assertEqual(record_recheck_budget(state, {"status": "UNCHANGED"}, now=NOW)["attempts"], [NOW])

    def test_pending_retry_state_is_queryable(self):
        states = [
            dict(self.target("a"), budget_state=self.state_with_attempts(5, deferred_until="2026-09-21T12:30:00+08:00", failure_streak=1)),
            dict(
                self.target("b"),
                budget_state=self.state_with_attempts(
                    5,
                    deferred_until="2026-09-21T11:00:00+08:00",
                    deadline_at=NOW,
                ),
            ),
            dict(self.target("c"), budget_state=self.state_with_attempts(5)),
        ]
        rows = pending_retry_rows(states, now=NOW)
        self.assertEqual([row["stable_key"] for row in rows], ["b", "a"])
        self.assertTrue(rows[0]["retry_deadline_exceeded"])
        self.assertEqual(rows[0]["seconds_until_retry"], 0)
        self.assertEqual(rows[1]["seconds_until_retry"], 1800)
        self.assertEqual(rows[1]["failure_streak"], 1)
        self.assertEqual(rows[1]["host"], "www.tccc.gov.tw")

    def test_invalid_budget_policy_and_target_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "per_source_limit"):
            plan_recheck_budget([self.target("a")], now=NOW, policy={"per_source_limit": 0})
        with self.assertRaisesRegex(ValueError, "backoff_deadline_hours"):
            plan_recheck_budget([self.target("a")], now=NOW, policy={"backoff_deadline_hours": 0})
        with self.assertRaisesRegex(ValueError, "stable_key"):
            plan_recheck_budget([{"source_id": "S-004", "host": "www.tccc.gov.tw"}], now=NOW)
        with self.assertRaisesRegex(ValueError, "failure_streak"):
            record_recheck_budget(
                {"attempts": [], "failure_streak": "many"}, {"status": "UNCHANGED"}, now=NOW
            )
        with self.assertRaisesRegex(ValueError, "run_limit"):
            plan_recheck_budget([self.target("a")], now=NOW, run_limit=-1)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            plan_recheck_budget([self.target("a")], now="2026-09-21 12:00:00")
        self.assertEqual(DEFAULT_RECHECK_BUDGET_POLICY["per_source_limit"], 3)
        self.assertEqual(DEFAULT_RECHECK_BUDGET_POLICY["per_host_limit"], 5)


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
