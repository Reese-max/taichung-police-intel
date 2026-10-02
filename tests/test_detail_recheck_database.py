from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import json
import unittest

import online_collect


class FakeResponse:
    status_code = 200
    url = "https://www.tccc.gov.tw/detail"
    headers = {"Content-Type": "text/html", "ETag": '"v1"'}

    def __init__(self, status_code=None, headers=None) -> None:
        self.closed = False
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.headers = headers

    def iter_content(self, chunk_size: int):
        yield b"<html><p>notice</p></html>"

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, response=None) -> None:
        self.calls = []
        self.response = response or FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class Result:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, row) -> None:
        self.rows = row if isinstance(row, list) else [row]
        self.sql = []

    def transaction(self):
        return nullcontext(self)

    def execute(self, statement, params=()):
        self.sql.append((statement, params))
        if "FROM detail_recheck_state" in statement:
            return Result(list(self.rows))
        return Result()


def due_row(stable_key="traffic-1", *, budget_state=None, next_check_at=None):
    return {
        "source_id": "S-004",
        "stable_key": stable_key,
        "requested_url": "https://www.tccc.gov.tw/detail",
        "last_checked_at": None,
        "next_check_at": next_check_at or datetime(2026, 9, 21, 11, 0, tzinfo=timezone.utc),
        "etag": None,
        "last_modified": None,
        "document_version_id": None,
        "body_sha256": None,
        "normalized_text_sha256": None,
        "attachments": [],
        "budget_state": {} if budget_state is None else budget_state,
    }


def budget_updates(connection):
    return [
        params
        for statement, params in connection.sql
        if "next_check_at = COALESCE(%s, next_check_at)" in statement
    ]


def full_state_updates(connection):
    return [
        params
        for statement, params in connection.sql
        if statement.lstrip().startswith("UPDATE detail_recheck_state") and "document_version_id = %s" in statement
    ]


class DetailRecheckDatabaseTests(unittest.TestCase):
    def test_retry_after_and_interval_are_bounded(self):
        now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            online_collect.detail_next_check(
                {"status": "DEFERRED", "retry_after_seconds": 120}, now
            ),
            datetime(2026, 9, 21, 12, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(
            online_collect.detail_next_check({"status": "UNAVAILABLE"}, now, interval_hours=24),
            datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(ValueError, "requires timezone"):
            online_collect.detail_next_check({"status": "UNAVAILABLE"}, datetime(2026, 9, 21, 12, 0))

    def test_due_target_persists_bounded_snapshot_and_baseline(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(due_row())
        result = online_collect.run_detail_rechecks(
            connection,
            FakeSession(),
            {"S-004": "SR-1"},
            observed,
        )
        self.assertEqual(result[0]["status"], "BASELINE")
        self.assertTrue(result[0]["snapshot_id"].startswith("DR-SR-1-"))
        self.assertEqual(result[0]["classification"]["status"], "BASELINE")
        self.assertNotIn("response_body", result[0]["classification"])
        update = next(params for statement, params in connection.sql if statement.lstrip().startswith("UPDATE detail_recheck_state"))
        self.assertEqual(update[8], "BASELINE")
        self.assertEqual(update[14], result[0]["snapshot_id"])
        budget = json.loads(update[16])
        self.assertEqual(budget["attempts"], ["2026-09-21T20:00:00+08:00"])

    def test_allowed_request_persists_the_budget_attempt(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(due_row())
        session = FakeSession()
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["status"], "BASELINE")
        self.assertEqual(len(session.calls), 1)
        budget = json.loads(full_state_updates(connection)[0][16])
        self.assertEqual(budget["attempts"], ["2026-09-21T20:00:00+08:00"])
        self.assertEqual(budget["last_decision"], "COMPLETED")

    def test_source_in_backoff_is_not_requested_again(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(
            due_row(budget_state={"deferred_until": "2026-09-21T21:00:00+08:00", "failure_streak": 2})
        )
        session = FakeSession()
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["status"], "SKIPPED")
        self.assertEqual(result[0]["reason"], "BACKOFF_ACTIVE")
        self.assertEqual(session.calls, [], "a backing-off source must not be re-crawled")
        self.assertEqual(full_state_updates(connection), [])
        skipped = budget_updates(connection)[0]
        self.assertEqual(
            skipped[0],
            datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc),
            "the retry time must become the next due cursor",
        )
        self.assertEqual(json.loads(skipped[1])["deferred_until"], "2026-09-21T21:00:00+08:00")

    def test_per_source_request_limit_caps_requests_in_one_run(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection([due_row("traffic-1"), due_row("traffic-2")])
        session = FakeSession()
        result = online_collect.run_detail_rechecks(
            connection,
            session,
            {"S-004": "SR-1"},
            observed,
            limit=2,
            budget_policy={"per_source_limit": 1},
        )
        self.assertEqual([item["status"] for item in result], ["BASELINE", "SKIPPED"])
        self.assertEqual(result[1]["reason"], "SOURCE_BUDGET_EXHAUSTED")
        self.assertEqual(len(session.calls), 1, "one source must not exceed its per-run request cap")
        self.assertEqual(json.loads(full_state_updates(connection)[0][16])["attempts"], ["2026-09-21T20:00:00+08:00"])

    def test_retry_deadline_blocked_target_is_never_requested(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(
            due_row(
                budget_state={
                    "deferred_until": "2026-09-21T11:00:00+08:00",
                    "first_deferred_at": "2026-09-20T12:00:00+08:00",
                    "deadline_at": "2026-09-21T12:00:00+08:00",
                }
            )
        )
        session = FakeSession()
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["reason"], "RETRY_DEADLINE_EXCEEDED")
        self.assertEqual(session.calls, [])
        self.assertTrue(json.loads(budget_updates(connection)[0][1])["retry_deadline_exceeded"])

    def test_429_persists_backoff_instead_of_re_crawling(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(due_row())
        session = FakeSession(FakeResponse(429, headers={"Retry-After": "120"}))
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["status"], "DEFERRED")
        self.assertTrue(result[0]["review_required"] is False)
        update = full_state_updates(connection)[0]
        budget = json.loads(update[16])
        self.assertEqual(budget["failure_streak"], 1)
        self.assertEqual(budget["deferred_until"], "2026-09-21T20:15:00+08:00")
        self.assertEqual(budget["deadline_at"], "2026-09-22T20:00:00+08:00")
        self.assertEqual(budget["last_decision"], "BACKED_OFF")
        self.assertFalse(budget["retry_deadline_exceeded"])
        self.assertEqual(
            update[1],
            datetime(2026, 9, 21, 12, 15, tzinfo=timezone.utc),
            "the backoff window must become the next due cursor so a 429 cannot become a re-crawl loop",
        )
        self.assertIsNone(update[14], "a deferred response has no body snapshot")
        self.assertIsNone(result[0]["snapshot_id"])

    def test_register_updates_url_without_accepting_an_unapproved_origin(self):
        connection = FakeConnection({})
        online_collect.register_detail_recheck(
            connection,
            "S-004",
            "traffic-1",
            "https://www.tccc.gov.tw/detail-v2",
            datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIn("requested_url = EXCLUDED.requested_url", connection.sql[0][0])
        with self.assertRaisesRegex(ValueError, "outside the approved source origin"):
            online_collect.register_detail_recheck(
                connection,
                "S-004",
                "traffic-2",
                "https://evil.test/detail",
                datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
