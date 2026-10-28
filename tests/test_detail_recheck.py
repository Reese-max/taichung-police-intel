import unittest
import hashlib
import ast
from contextlib import nullcontext
from pathlib import Path
import json
import urllib.parse
import requests
from datetime import datetime, timedelta

from intel_v2.detail_recheck import classify_observation, plan_recheck
from intel_v2.detail_recheck_budget import (
    DEFAULT_RECHECK_BUDGET_POLICY,
    acknowledge_recheck_budget,
    empty_budget_state,
    pending_retry_rows,
    plan_recheck_budget,
    record_recheck_budget,
    budget_state_for_storage,
    recheck_budget_policy,
)
from intel_v2.detail_recheck_http import recheck_detail


NOW = "2026-09-21T12:00:00+08:00"
BEFORE = {
    "document_version_id": "DOCV-OLD",
    "body_sha256": "a" * 64,
    "normalized_text_sha256": "b" * 64,
    "attachments": [{"attachment_id": "pdf-1", "content_sha256": "c" * 64}],
    "observed_at": "2026-09-20T12:00:00+08:00",
    "last_checked_at": "2026-09-20T12:00:00+08:00",
    "etag": '"old"',
    "last_modified": "Wed, 20 Sep 2026 04:00:00 GMT",
    "expires_at": "2026-09-22T12:00:00+08:00",
}


class DetailRecheckTests(unittest.TestCase):
    def test_semantic_only_correction_retains_evidence_and_is_idempotent(self):
        for field in ("title", "location", "status", "scope"):
            with self.subTest(field=field):
                observation = {
                    "body_sha256": "a" * 64,
                    "normalized_text_sha256": "b" * 64,
                    "attachments": [],
                    field: "original",
                }
                baseline = classify_observation(None, observation, observed_at=NOW)["after"]
                correction = dict(observation, **{field: "corrected"})
                result = classify_observation(baseline, correction, observed_at=NOW)
                self.assertEqual(result["status"], "MATERIAL_CHANGE")
                self.assertTrue(result["review_required"])
                self.assertEqual(result["changed_fields"], [field])
                self.assertEqual(result["before"][field], "original")
                self.assertEqual(result["after"][field], "corrected")
                self.assertNotEqual(baseline["document_version_id"], result["after"]["document_version_id"])
                repeated = classify_observation(result["after"], correction, observed_at=NOW)
                self.assertEqual(repeated["status"], "UNCHANGED")
                self.assertFalse(repeated["review_required"])
                self.assertEqual(repeated["after"]["document_version_id"], result["after"]["document_version_id"])

    def test_unchanged_semantic_fields_do_not_reopen_review(self):
        previous = dict(BEFORE, title="公告", location="臺中市", status="ACTIVE", scope="道路")
        result = classify_observation(previous, previous, observed_at=NOW)
        self.assertEqual(result["status"], "UNCHANGED")
        self.assertFalse(result["review_required"])

    def test_due_plan_preserves_conditional_request_headers(self):
        result = plan_recheck(BEFORE, NOW, interval_hours=24)
        self.assertEqual(result["status"], "DUE")
        self.assertEqual(result["reason"], "TTL_EXPIRED")
        self.assertEqual(result["request_headers"]["If-None-Match"], '"old"')
        self.assertEqual(result["request_headers"]["If-Modified-Since"], BEFORE["last_modified"])

    def test_full_recheck_interval_is_configurable_and_validated(self):
        result = plan_recheck(BEFORE, NOW, full_recheck_interval_hours=24)
        self.assertEqual(result["request_headers"], {})
        for invalid in (True, 0, -1, 1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                plan_recheck(BEFORE, NOW, full_recheck_interval_hours=invalid)

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
    def test_sentence_punctuation_is_presentation_but_time_and_decimal_changes_need_review(self):
        for old, new, expected in (
            ("Control 17:00.", "Control 17:00!", "PRESENTATION_ONLY"),
            ("管制 17:00。", "管制 17:00！", "PRESENTATION_ONLY"),
            ("Control 17:00.", "Control 16:00!", "MATERIAL_CHANGE"),
            ("Range 1.5 km.", "Range 15 km!", "MATERIAL_CHANGE"),
        ):
            with self.subTest(old=old, new=new):
                session = FakeHTTPSession(*(FakeHTTPResponse(body=f"<p>{text}</p>".encode("utf-8"),
                    headers={"Content-Type": "text/html"}) for text in (old, new)))
                baseline = recheck_detail(session, "https://official.test/detail", None,
                    observed_at=NOW, allowed_hosts={"official.test"})["classification"]["after"]
                result = recheck_detail(session, "https://official.test/detail", baseline,
                    observed_at="2026-09-22T12:00:00+08:00", allowed_hosts={"official.test"})["classification"]
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["review_required"], expected == "MATERIAL_CHANGE")
                self.assertNotEqual(result["before"]["document_version_id"], result["after"]["document_version_id"])

    def test_periodic_full_recheck_omits_unreliable_validators(self):
        for full_observation, full_interval in (
            (None, 168), ("2026-09-13T12:00:00+08:00", 168), (BEFORE["observed_at"], 24),
        ):
            with self.subTest(full_observation=full_observation, full_interval=full_interval):
                previous = dict(BEFORE, observed_at=full_observation)
                session = FakeHTTPSession(FakeHTTPResponse(body=b"<p>control 16:00</p>"))
                result = recheck_detail(session, "https://official.test/detail", previous,
                    observed_at=NOW, allowed_hosts={"official.test"}, full_recheck_interval_hours=full_interval)
                self.assertEqual(session.calls[0][1]["headers"], {})
                self.assertEqual(result["classification"]["status"], "MATERIAL_CHANGE")
                self.assertEqual(result["classification"]["after"]["observed_at"], NOW)

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
        self.assertTrue(result["classification"]["attachment_recheck_required"])
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


class DetailRecheckRollingWindowTests(unittest.TestCase):
    target = DetailRecheckBudgetTests.target
    state_with_attempts = DetailRecheckBudgetTests.state_with_attempts

    def test_expired_history_restores_the_next_window(self):
        old = self.target("old", budget_state=self.state_with_attempts(120, 121, 122))
        decision = plan_recheck_budget([self.target("new")], now=NOW, historical_targets=[old])[0]
        self.assertEqual(decision["reason"], "ALLOWED")
        self.assertEqual(decision["source_attempts_in_window"], 0)

    def test_same_target_in_history_is_charged_once(self):
        target = self.target("same", budget_state=self.state_with_attempts(10))
        decision = plan_recheck_budget([target], now=NOW, historical_targets=[target], policy={"per_source_limit": 2})[0]
        self.assertEqual(decision["reason"], "ALLOWED")
        self.assertEqual(decision["source_attempts_in_window"], 1)

    def test_duplicate_identity_is_rejected_before_budget_admission(self):
        with self.assertRaisesRegex(ValueError, "identities must be unique"):
            plan_recheck_budget([self.target("same"), self.target("same")], now=NOW)

    def test_poisoned_large_failure_count_keeps_backoff_bounded(self):
        after = record_recheck_budget(self.state_with_attempts(failure_streak=1_000_000), {"status": "DEFERRED"}, now=NOW)
        self.assertEqual(after["deferred_until"], "2026-09-21T18:00:00+08:00")


class AttachmentContentRecheckTests(unittest.TestCase):
    url = "https://official.test/detail"
    pdf = "https://official.test/files/a.pdf"
    body = b"<p>notice</p><a href='/files/a.pdf'>PDF</a>"

    def previous(self, count=1):
        baseline = recheck_detail(FakeHTTPSession(FakeHTTPResponse(body=self.body)),
            self.url, None, observed_at=BEFORE["observed_at"],
            allowed_hosts={"official.test"})["classification"]["after"]
        baseline["attachments"] = [{
            "attachment_id": "ATT-" + hashlib.sha256(
                f"https://official.test/files/{name}.pdf".encode()).hexdigest()[:20].upper(),
            "url": f"https://official.test/files/{name}.pdf",
            "content_sha256": hashlib.sha256(b"old").hexdigest(),
            "etag": '"pdf-old"',
        } for name in "abc"[:count]]
        return baseline

    def fetch(self, previous, *responses, **kwargs):
        session = FakeHTTPSession(*responses)
        result = recheck_detail(session, self.url, previous, observed_at=NOW,
            allowed_hosts={"official.test", "other.test"}, **kwargs)
        return result, session

    def test_same_url_attachment_change_on_304_and_identical_200_requires_review(self):
        for response in (FakeHTTPResponse(304), FakeHTTPResponse(body=self.body)):
            with self.subTest(status=response.status_code):
                previous = self.previous()
                result, session = self.fetch(previous, response,
                    FakeHTTPResponse(body=b"new", url=self.pdf))
                self.assertEqual(result["classification"]["status"], "ATTACHMENT_CHANGED")
                self.assertTrue(result["classification"]["review_required"])
                self.assertTrue(result["observation"]["attachments_checked"])
                self.assertEqual(result["request_count"], 2)
                self.assertEqual(session.calls[1][0], self.pdf)
                self.assertNotEqual(result["classification"]["before"]["attachments"][0]["content_sha256"],
                    result["classification"]["after"]["attachments"][0]["content_sha256"])
                if response.status_code == 304:
                    self.assertEqual(result["classification"]["after"]["observed_at"], previous["observed_at"])
                self.assertEqual(previous["attachments"][0]["etag"], '"pdf-old"')

    def test_attachment_304_keeps_hash_and_uses_its_own_validators(self):
        previous = self.previous()
        result, session = self.fetch(previous, FakeHTTPResponse(304),
            FakeHTTPResponse(304, url=self.pdf))
        self.assertTrue(result["observation"]["attachments_checked"])
        self.assertFalse(result["classification"]["attachment_recheck_required"])
        self.assertFalse(result["classification"]["review_required"])
        self.assertEqual(result["classification"]["after"]["document_version_id"], previous["document_version_id"])
        self.assertEqual(result["observation"]["attachments"],
            [dict(previous["attachments"][0], last_checked_at=NOW, last_attempted_at=NOW)])
        self.assertEqual(session.calls[1][1]["headers"], {"If-None-Match": '"pdf-old"'})

    def test_partial_budget_reports_checked_and_unchecked_and_keeps_review(self):
        previous = self.previous(3)
        result, session = self.fetch(previous, FakeHTTPResponse(304),
            FakeHTTPResponse(body=b"new", url=self.pdf), max_requests=2)
        self.assertEqual(len(session.calls), 2)
        self.assertFalse(result["observation"]["attachments_checked"])
        self.assertEqual([row["reason"] for row in result["observation"]["attachment_checks"]],
            ["CHECKED", "REQUEST_BUDGET_EXHAUSTED", "REQUEST_BUDGET_EXHAUSTED"])
        self.assertTrue(result["classification"]["review_required"])
        self.assertTrue(result["classification"]["attachment_recheck_required"])
        self.assertEqual(result["observation"]["attachments"][1:],
            sorted(previous["attachments"][1:], key=lambda row: row["attachment_id"]))

    def test_attachment_errors_preserve_lkg_and_retry_after(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                previous = self.previous()
                responses = [FakeHTTPResponse(304),
                    FakeHTTPResponse(status, url=self.pdf, headers={"Retry-After": "120"})]
                result, session = self.fetch(previous, *responses)
                self.assertFalse(result["observation"]["attachments_checked"])
                self.assertEqual(result["observation"]["attachments"],
                    [dict(previous["attachments"][0], last_attempted_at=NOW)])
                self.assertEqual(result["classification"]["status"], "DEFERRED")
                self.assertEqual(result["classification"]["retry_after_seconds"], 120)
                self.assertEqual(result["request_count"], 2)
                self.assertTrue(all(response.closed for response in responses))

    def test_redirects_and_sizes_use_the_same_transport_bounds(self):
        cases = (
            ([FakeHTTPResponse(302, headers={"Location": "https://evil.test/a.pdf"})], {}, 2),
            ([FakeHTTPResponse(body=b"12345", url=self.pdf)], {"max_body_bytes": 4}, 2),
            ([FakeHTTPResponse(body=b"x", url=self.pdf, headers={"Content-Length": "5"})],
                {"max_body_bytes": 4}, 2),
            ([FakeHTTPResponse(302, headers={"Location": "/files/b.pdf"})], {"max_requests": 2}, 2),
            ([FakeHTTPResponse(302, headers={"Location": "/files/b.pdf"})], {"max_redirects": 0}, 2),
        )
        for responses, kwargs, calls in cases:
            with self.subTest(kwargs=kwargs, headers=responses[0].headers):
                previous = self.previous()
                result, session = self.fetch(previous, FakeHTTPResponse(304), *responses, **kwargs)
                self.assertEqual(len(session.calls), calls)
                self.assertFalse(result["observation"]["attachments_checked"])
                self.assertEqual(result["observation"]["attachments"],
                    [dict(previous["attachments"][0], last_attempted_at=NOW)])
                self.assertTrue(all(response.closed for response in responses))

    def test_same_host_redirect_is_charged_and_all_responses_close(self):
        responses = [FakeHTTPResponse(304), FakeHTTPResponse(302, headers={"Location": "/files/b.pdf"}),
            FakeHTTPResponse(body=b"old", url="https://official.test/files/b.pdf")]
        result, session = self.fetch(self.previous(), *responses, max_requests=3)
        self.assertTrue(result["observation"]["attachments_checked"])
        self.assertEqual(result["request_count"], 3)
        self.assertEqual(session.calls[-1][0], "https://official.test/files/b.pdf")
        self.assertTrue(all(response.closed for response in responses))

    def test_missing_legacy_url_is_explicitly_unchecked(self):
        previous = self.previous()
        del previous["attachments"][0]["url"]
        result, session = self.fetch(previous, FakeHTTPResponse(304))
        self.assertFalse(result["observation"]["attachments_checked"])
        self.assertEqual(result["observation"]["attachment_checks"][0]["reason"], "MISSING_URL")
        self.assertEqual(len(session.calls), 1)

    def test_hashes_and_urls_survive_version_round_trip_without_false_review(self):
        previous = self.previous()
        result, _ = self.fetch(previous, FakeHTTPResponse(body=self.body),
            FakeHTTPResponse(body=b"old", url=self.pdf, headers={"ETag": '"pdf-new"'}))
        self.assertEqual(result["classification"]["status"], "UNCHANGED")
        after = result["classification"]["after"]
        self.assertEqual(after["document_version_id"], previous["document_version_id"])
        self.assertEqual(after["attachments"][0]["url"], self.pdf)
        self.assertEqual(after["attachments"][0]["etag"], '"pdf-new"')
        session = FakeHTTPSession(FakeHTTPResponse(304), FakeHTTPResponse(304, url=self.pdf))
        again = recheck_detail(session, self.url, after, observed_at="2026-09-22T12:00:00+08:00",
            allowed_hosts={"official.test"})
        self.assertTrue(again["observation"]["attachments_checked"])
        self.assertEqual(session.calls[1][1]["headers"], {"If-None-Match": '"pdf-new"'})

    def test_each_get_is_retained_in_the_existing_rolling_ledger(self):
        state = record_recheck_budget(None, {"status": "UNCHANGED"}, now=NOW, request_count=3)
        self.assertEqual(state["attempts"], [NOW])
        self.assertEqual(state["request_counts"], {NOW: 3})
        decision = plan_recheck_budget([DetailRecheckBudgetTests().target("a", budget_state=state)], now=NOW)[0]
        self.assertEqual(decision["reason"], "SOURCE_BUDGET_EXHAUSTED")
        self.assertEqual(decision["source_attempts_in_window"], 3)
        deferred = record_recheck_budget(None, {"status": "DEFERRED"}, now=NOW, request_count=3)
        pending = pending_retry_rows([DetailRecheckBudgetTests().target("a", budget_state=deferred)], now=NOW)
        self.assertEqual(pending[0]["attempts_in_window"], 3)

    def test_budget_exhausted_before_any_attachment_keeps_every_hash(self):
        previous = self.previous(2)
        result, session = self.fetch(previous, FakeHTTPResponse(304), max_requests=1)
        self.assertEqual(result["observation"]["attachments"], previous["attachments"])
        self.assertFalse(result["observation"]["attachments_checked"])
        self.assertTrue(all(row["reason"] == "REQUEST_BUDGET_EXHAUSTED"
            for row in result["observation"]["attachment_checks"]))
        self.assertEqual(len(session.calls), 1)

    def test_first_download_without_known_hash_is_unconditional(self):
        for status in (304, 200):
            with self.subTest(status=status):
                previous = self.previous()
                del previous["attachments"][0]["content_sha256"]
                result, session = self.fetch(previous, FakeHTTPResponse(status, body=self.body),
                    FakeHTTPResponse(body=b"old", url=self.pdf))
                self.assertEqual(session.calls[1][1]["headers"], {})
                self.assertTrue(result["observation"]["attachments_checked"])
                classification = result["classification"]
                self.assertEqual(classification["status"], "NOT_MODIFIED" if status == 304 else "UNCHANGED")
                self.assertFalse(classification["review_required"])
                self.assertEqual(classification["attachment_baseline_ids"],
                    [previous["attachments"][0]["attachment_id"]])
                after = classification["after"]
                self.assertEqual(after["document_version_id"], previous["document_version_id"])
                self.assertEqual(after["attachments"][0]["content_sha256"], hashlib.sha256(b"old").hexdigest())
                updated = recheck_detail(FakeHTTPSession(FakeHTTPResponse(304),
                    FakeHTTPResponse(body=b"new", url=self.pdf)), self.url, after,
                    observed_at="2026-09-22T12:00:00+08:00", allowed_hosts={"official.test"})
                self.assertEqual(updated["classification"]["status"], "ATTACHMENT_CHANGED")
                self.assertTrue(updated["classification"]["review_required"])
                self.assertEqual(updated["classification"]["attachment_baseline_ids"], [])

    def test_partial_budget_rotates_all_attachments_across_version_round_trips(self):
        body = b"<p>notice</p>" + b"".join(
            f"<a href='/files/{name}.pdf'>PDF</a>".encode() for name in "abc")
        previous = self.previous(3)
        body_version = recheck_detail(FakeHTTPSession(FakeHTTPResponse(body=body)), self.url, None,
            observed_at=BEFORE["observed_at"], allowed_hosts={"official.test"})["classification"]["after"]
        for key in ("body_sha256", "normalized_text_sha256", "material_text_sha256"):
            previous[key] = body_version[key]
        expected = [row["url"] for row in previous["attachments"]]
        checked = []
        for round_number in range(6):
            now = (datetime.fromisoformat(NOW) + timedelta(days=round_number)).isoformat()
            session = FakeHTTPSession(FakeHTTPResponse(304 if round_number % 2 == 0 else 200, body=body),
                FakeHTTPResponse(304 if round_number % 2 == 0 else 200, body=b"old",
                    url=""))
            result = recheck_detail(session, self.url, previous, observed_at=now,
                allowed_hosts={"official.test"}, max_requests=2)
            checked.append(session.calls[1][0])
            self.assertNotIn(checked[-1], checked[(round_number // 3) * 3:-1])
            self.assertEqual(result["request_count"], 2)
            self.assertFalse(result["observation"]["attachments_checked"])
            self.assertEqual([row["reason"] for row in result["observation"]["attachment_checks"]],
                ["CHECKED", "REQUEST_BUDGET_EXHAUSTED", "REQUEST_BUDGET_EXHAUSTED"])
            after = result["classification"]["after"]
            self.assertEqual(next(row for row in after["attachments"] if row["url"] == checked[-1])[
                "last_checked_at"], now)
            self.assertFalse(result["classification"]["review_required"])
            # Simulate persisted JSON and a fresh worker each round.
            previous = json.loads(json.dumps(after))
        self.assertEqual(set(checked[:3]), set(expected))
        self.assertEqual(set(checked[3:]), set(expected))

    def test_permanent_404_rotates_with_healthy_attachments_after_json_round_trip(self):
        for max_requests in (2, 3):
            with self.subTest(max_requests=max_requests):
                previous = self.previous(3)
                expected = sorted(previous["attachments"], key=lambda row: row["attachment_id"])
                urls = [row["url"] for row in expected]

                class RoutingSession(FakeHTTPSession):
                    def get(session, url, **kwargs):
                        session.calls.append((url, kwargs))
                        return FakeHTTPResponse(404 if url == urls[0] else 304, url=url)

                attempted, checked, versions = [], set(), []
                for round_number in range(6):
                    now = (datetime.fromisoformat(NOW) + timedelta(days=round_number)).isoformat()
                    session = RoutingSession()
                    result = recheck_detail(session, self.url, previous, observed_at=now,
                        allowed_hosts={"official.test"}, max_requests=max_requests)
                    attempted.extend(url for url, _ in session.calls[1:])
                    checked.update(row["attachment_id"] for row in result["observation"]["attachment_checks"]
                        if row["checked"])
                    self.assertEqual(result["request_count"], max_requests)
                    self.assertFalse(result["observation"]["attachments_checked"])
                    self.assertFalse(result["classification"]["review_required"])
                    versions.append(result["classification"]["after"])
                    previous = json.loads(json.dumps(versions[-1]))
                self.assertEqual(attempted[:max_requests - 1], urls[:max_requests - 1])
                # Two attempts in one round share a timestamp; ID breaks that tie.
                window = 3 if max_requests == 2 else 4
                for start in range(0, len(attempted), window):
                    self.assertEqual(set(attempted[start:start + window]), set(urls))
                self.assertTrue(any(attempted[start] != urls[0]
                    for start in range(0, len(attempted), max_requests - 1)))
                self.assertEqual(checked, {row["attachment_id"] for row in expected[1:]})
                for round_number, after in enumerate(versions):
                    now = (datetime.fromisoformat(NOW) + timedelta(days=round_number)).isoformat()
                    round_urls = attempted[round_number * (max_requests - 1):(round_number + 1) * (max_requests - 1)]
                    for row in after["attachments"]:
                        if row["url"] in round_urls:
                            self.assertEqual(row["last_attempted_at"], now)
                        if row["url"] == urls[0]:
                            self.assertNotIn("last_checked_at", row)

    def test_attempt_metadata_is_optional_and_does_not_change_version_hash(self):
        observation = dict(BEFORE, attachments=[{"attachment_id": "legacy"}])
        baseline = classify_observation(None, observation, observed_at=NOW)["after"]
        self.assertEqual(baseline["attachments"], [{"attachment_id": "legacy"}])
        observation["attachments"][0]["last_attempted_at"] = "2026-09-21T04:00:00Z"
        enriched = classify_observation(None, observation, observed_at=NOW)["after"]
        self.assertEqual(enriched["attachments"],
            [{"attachment_id": "legacy", "last_attempted_at": NOW}])
        self.assertEqual(enriched["document_version_id"], baseline["document_version_id"])

    def test_failed_attempts_update_attempt_time_without_overwriting_success_or_lkg(self):
        cases = (
            (FakeHTTPResponse(404), {}, self.pdf, "HTTP_ERROR"),
            (FakeHTTPResponse(429), {}, self.pdf, "DEFERRED"),
            (FakeHTTPResponse(503), {}, self.pdf, "DEFERRED"),
            (FakeHTTPResponse(body=b"12345"), {"max_body_bytes": 4}, self.pdf, "bounded byte limit"),
            (FakeHTTPResponse(302, headers={"Location": "https://evil.test/a.pdf"}),
                {}, self.pdf, "outside the approved"),
            (None, {}, "https://evil.test/a.pdf", "outside the approved"),
        )
        for response, kwargs, url, reason in cases:
            with self.subTest(reason=reason, status=response.status_code if response else None):
                previous = self.previous()
                previous["attachments"][0].update(url=url, last_checked_at=BEFORE["observed_at"])
                result, _ = self.fetch(previous, FakeHTTPResponse(304),
                    *([response] if response else []), **kwargs)
                self.assertIn(reason, result["observation"]["attachment_checks"][0]["reason"])
                expected = [dict(previous["attachments"][0], last_attempted_at=NOW)]
                self.assertEqual(result["observation"]["attachments"], expected)
                self.assertEqual(result["classification"]["after"]["attachments"], expected)
                self.assertEqual(result["classification"]["after"]["document_version_id"],
                    previous["document_version_id"])
                self.assertNotIn("last_attempted_at", previous["attachments"][0])

    def test_redirects_between_approved_hosts_work_for_body_and_attachments(self):
        redirected = "https://other.test/redirected"
        for attachment in (False, True):
            with self.subTest(attachment=attachment):
                responses = ([FakeHTTPResponse(304)] if attachment else []) + [
                    FakeHTTPResponse(302, headers={"Location": redirected}),
                    FakeHTTPResponse(body=b"old" if attachment else b"<p>notice</p>", url=redirected)]
                result, session = self.fetch(self.previous() if attachment else None, *responses)
                self.assertEqual([call[0] for call in session.calls],
                    ([self.url, self.pdf, redirected] if attachment else [self.url, redirected]))
                self.assertEqual(result["request_count"], len(responses))
                self.assertTrue(all(response.closed for response in responses))
                if attachment:
                    self.assertTrue(result["observation"]["attachments_checked"])
                    self.assertFalse(result["classification"]["review_required"])
                else:
                    self.assertEqual(result["classification"]["status"], "BASELINE")
                    self.assertEqual(result["final_url"], redirected)

    def test_redirect_from_approved_second_host_to_unapproved_host_is_refused(self):
        redirected = "https://other.test/redirected"
        for attachment in (False, True):
            with self.subTest(attachment=attachment):
                responses = ([FakeHTTPResponse(304)] if attachment else []) + [
                    FakeHTTPResponse(302, headers={"Location": redirected}),
                    FakeHTTPResponse(302, url=redirected, headers={"Location": "https://evil.test/a.pdf"})]
                result, session = self.fetch(self.previous() if attachment else None, *responses)
                self.assertEqual(len(session.calls), len(responses))
                self.assertEqual(result["request_count"], len(responses))
                self.assertTrue(all(response.closed for response in responses))
                if attachment:
                    self.assertFalse(result["observation"]["attachments_checked"])
                    self.assertIn("outside the approved", result["observation"]["attachment_checks"][0]["reason"])
                else:
                    self.assertEqual(result["classification"]["status"], "UNAVAILABLE")
                    self.assertIn("outside the approved", result["transport_error"]["message"])

    def test_changed_attachment_and_then_429_keeps_review_and_stops_siblings(self):
        previous = self.previous(3)
        result, session = self.fetch(previous, FakeHTTPResponse(304), FakeHTTPResponse(body=b"new", url=self.pdf),
            FakeHTTPResponse(429, headers={"Retry-After": "7200"}))
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(result["classification"]["status"], "ATTACHMENT_CHANGED")
        self.assertTrue(result["classification"]["review_required"])
        self.assertFalse(result["observation"]["attachments_checked"])
        self.assertEqual(result["observation"]["attachment_checks"][-1]["reason"], "SOURCE_BACKOFF")
        skipped_id = result["observation"]["attachment_checks"][-1]["attachment_id"]
        self.assertNotIn("last_attempted_at", next(row for row in result["classification"]["after"]["attachments"]
            if row["attachment_id"] == skipped_id))
        state = record_recheck_budget(None, result["classification"], now=NOW, request_count=3)
        self.assertEqual(state["deferred_until"], "2026-09-21T14:00:00+08:00")

    def test_attachment_failure_and_unsafe_stored_url_keep_lkg(self):
        for value in ("https://evil.test/a.pdf", "http://official.test/a.pdf", "https://official.test:8443/a.pdf"):
            with self.subTest(url=value):
                previous = self.previous()
                previous["attachments"][0]["url"] = value
                result, session = self.fetch(previous, FakeHTTPResponse(304))
                self.assertEqual(len(session.calls), 1)
                self.assertFalse(result["observation"]["attachments_checked"])
                self.assertEqual(result["observation"]["attachments"],
                    [dict(previous["attachments"][0], last_attempted_at=NOW)])
        previous = self.previous()
        result, _ = self.fetch(previous, FakeHTTPResponse(304), FakeHTTPResponse(404, url=self.pdf))
        self.assertFalse(result["observation"]["attachments_checked"])
        self.assertEqual(result["observation"]["attachments"],
            [dict(previous["attachments"][0], last_attempted_at=NOW)])

    def collector_harness(self):
        # Load the actual collector functions without its optional psycopg import.
        import intel_v2.detail_recheck as core
        names = {"_detail_previous", "detail_next_check", "_detail_budget_host", "_detail_json",
            "_defer_detail_recheck", "_detail_recheck_outcome", "_detail_unavailable", "run_detail_rechecks"}
        source = Path(__file__).resolve().parents[1] / "online_collect.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names)], type_ignores=[])
        env = dict(globals(), FULL_RECHECK_INTERVAL_HOURS=168, SEMANTIC_FIELDS=core.SEMANTIC_FIELDS,
            DETAIL_RECHECK_MAX_PER_RUN=1, DETAIL_RECHECK_INTERVAL_HOURS=24,
            timestamp=lambda value: value.isoformat(timespec="seconds"),
            validate_document_url=lambda *_: {"entrypoint": self.url},
            _save_detail_snapshot=lambda *_: None)
        exec(compile(ast.fix_missing_locations(module), str(source), "exec"), env)

        class Connection:
            def __init__(self, rows, historical_rows=None):
                self.rows, self.writes = rows, []
                self.historical_rows = rows if historical_rows is None else historical_rows

            def transaction(self):
                return nullcontext()

            def execute(self, sql, params=()):
                self.writes.append((sql, params))
                self.result_rows = self.rows if "LIMIT %s" in sql else self.historical_rows
                return self

            def fetchall(self):
                return self.result_rows

        return env["run_detail_rechecks"], Connection

    def test_collector_malformed_candidate_blocks_only_its_own_row_in_either_order(self):
        run, Connection = self.collector_harness()
        for bad_first in (True, False):
            with self.subTest(bad_first=bad_first):
                rows = [dict(source_id="s", stable_key=key, requested_url=self.url,
                    budget_state=state) for key, state in (
                        ("bad", {"attempts": "oops"}), ("good", {}))]
                if not bad_first:
                    rows.reverse()
                connection = Connection(rows)
                session = FakeHTTPSession(FakeHTTPResponse(body=self.body))
                outcomes = run(connection, session, {"s": "run"}, datetime.fromisoformat(NOW), limit=2)
                expected_reasons = ["INVALID_BUDGET_STATE", None] if bad_first else [None, "INVALID_BUDGET_STATE"]
                self.assertEqual([item["reason"] for item in outcomes], expected_reasons)
                self.assertEqual([item["status"] for item in outcomes],
                    ["SKIPPED", "BASELINE"] if bad_first else ["BASELINE", "SKIPPED"])
                self.assertEqual(len(session.calls), 1)
                refused = next(item for item in outcomes if item["stable_key"] == "bad")
                self.assertEqual(refused["budget"]["state"]["attempts"], [])
                stored = next(json.loads(params[1]) for sql, params in connection.writes
                    if "GREATEST(next_check_at, COALESCE(%s, next_check_at))" in sql)
                self.assertEqual(stored, refused["budget"]["state"])
                self.assertEqual(rows[0 if bad_first else 1]["budget_state"], {"attempts": "oops"})

    def test_collector_unreadable_history_outside_candidates_still_refuses_requests(self):
        run, Connection = self.collector_harness()
        row = dict(source_id="s", stable_key="good", requested_url=self.url, budget_state={})
        history = dict(row, stable_key="historical", budget_state={"attempts": "oops"})
        session = FakeHTTPSession()
        outcomes = run(Connection([row], [history]), session, {"s": "run"}, datetime.fromisoformat(NOW), limit=2)
        self.assertEqual(outcomes[0]["reason"], "SOURCE_BUDGET_EXHAUSTED")
        self.assertEqual(session.calls, [])

    def test_collector_charges_attachment_gets_before_admitting_another_target(self):
        run, Connection = self.collector_harness()

        for limit, policy, sources, reason in (
            (2, {}, ("s", "s"), "RUN_LIMIT_REACHED"),
            (10, {"per_source_limit": 2}, ("s", "s"), "SOURCE_BUDGET_EXHAUSTED"),
            (10, {"per_host_limit": 2}, ("s", "t"), "HOST_BUDGET_EXHAUSTED"),
        ):
            with self.subTest(reason=reason):
                rows = [dict(self.previous(), source_id=source_id, stable_key=str(index),
                    requested_url=self.url, budget_state={}) for index, source_id in enumerate(sources)]
                connection = Connection(rows)
                session = FakeHTTPSession(FakeHTTPResponse(304), FakeHTTPResponse(body=b"new", url=self.pdf))
                outcomes = run(connection, session,
                    {source: "run" for source in sources}, datetime.fromisoformat(NOW), limit=limit, budget_policy=policy)
                self.assertEqual(len(session.calls), 2)
                self.assertTrue(outcomes[0]["review_required"])
                self.assertEqual(outcomes[1]["reason"], reason)
                self.assertEqual(outcomes[0]["budget"]["state"]["request_counts"], {NOW: 2})
                self.assertTrue(any("UPDATE detail_recheck_state" in sql for sql, _ in connection.writes))

    def test_collector_default_limit_leaves_attachment_unchecked(self):
        run, Connection = self.collector_harness()
        row = dict(self.previous(), source_id="s", stable_key="a", requested_url=self.url, budget_state={})
        connection = Connection([row])
        session = FakeHTTPSession(FakeHTTPResponse(304))
        result = run(connection, session, {"s": "run"}, datetime.fromisoformat(NOW))[0]
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(result["review_required"])
        self.assertTrue(result["classification"]["attachment_recheck_required"])
        stored = next(json.loads(params[13]) for sql, params in connection.writes
            if "document_version_id = %s" in sql)
        self.assertFalse(stored["observation"]["attachments_checked"])
        self.assertEqual(stored["observation"]["attachment_checks"][0]["reason"], "REQUEST_BUDGET_EXHAUSTED")

    def test_adapter_retries_cannot_multiply_a_budgeted_get(self):
        class RetryingSession(FakeHTTPSession):
            max_retries = 2

            def get_adapter(self, url):
                return self

            def get(self, url, **kwargs):
                if self.max_retries != 0:
                    raise AssertionError("automatic retries exceed the admitted GET budget")
                return super().get(url, **kwargs)

        session = RetryingSession(FakeHTTPResponse(304), FakeHTTPResponse(304, url=self.pdf))
        result = recheck_detail(session, self.url, self.previous(), observed_at=NOW,
            allowed_hosts={"official.test"})
        self.assertTrue(result["observation"]["attachments_checked"])
        self.assertEqual(session.max_retries, 2)

        def timeout(url, **kwargs):
            session.calls.append((url, kwargs))
            self.assertEqual(session.max_retries, 0)
            raise requests.Timeout("offline timeout")

        session.get = timeout
        failed = recheck_detail(session, self.url, self.previous(), observed_at=NOW,
            allowed_hosts={"official.test"})
        self.assertEqual(failed["classification"]["status"], "UNAVAILABLE")
        self.assertEqual(failed["request_count"], 1)
        self.assertEqual(session.max_retries, 2)

    def test_attachment_timeout_keeps_lkg_and_counts_failed_get(self):
        class TimeoutSession(FakeHTTPSession):
            def get(self, url, **kwargs):
                if url == AttachmentContentRecheckTests.pdf:
                    self.calls.append((url, kwargs))
                    raise requests.Timeout("offline attachment timeout")
                return super().get(url, **kwargs)

        previous = self.previous()
        session = TimeoutSession(FakeHTTPResponse(304))
        result = recheck_detail(session, self.url, previous, observed_at=NOW, allowed_hosts={"official.test"})
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["observation"]["attachments"],
            [dict(previous["attachments"][0], last_attempted_at=NOW)])
        self.assertFalse(result["observation"]["attachments_checked"])
        self.assertIn("timeout", result["observation"]["attachment_checks"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
