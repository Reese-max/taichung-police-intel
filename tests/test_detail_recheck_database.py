from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import unittest

import online_collect


class FakeResponse:
    status_code = 200
    url = "https://www.tccc.gov.tw/detail"
    headers = {"Content-Type": "text/html", "ETag": '"v1"'}

    def __init__(self) -> None:
        self.closed = False

    def iter_content(self, chunk_size: int):
        yield b"<html><p>notice</p></html>"

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


class Result:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, row) -> None:
        self.row = row
        self.sql = []

    def transaction(self):
        return nullcontext(self)

    def execute(self, statement, params=()):
        self.sql.append((statement, params))
        if "FROM detail_recheck_state" in statement:
            return Result([self.row])
        return Result()


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
        connection = FakeConnection(
            {
                "source_id": "S-004",
                "stable_key": "traffic-1",
                "requested_url": "https://www.tccc.gov.tw/detail",
                "last_checked_at": None,
                "next_check_at": datetime(2026, 9, 21, 11, 0, tzinfo=timezone.utc),
                "etag": None,
                "last_modified": None,
                "document_version_id": None,
                "body_sha256": None,
                "normalized_text_sha256": None,
                "attachments": [],
            }
        )
        result = online_collect.run_detail_rechecks(
            connection,
            FakeSession(),
            {"S-004": "SR-1"},
            observed,
        )
        self.assertEqual(result[0]["status"], "BASELINE")
        self.assertTrue(result[0]["snapshot_id"].startswith("DR-SR-1-"))
        update = next(params for statement, params in connection.sql if statement.lstrip().startswith("UPDATE detail_recheck_state"))
        self.assertEqual(update[8], "BASELINE")
        self.assertEqual(update[14], result[0]["snapshot_id"])

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
