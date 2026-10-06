from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import json
import re
import unittest

import online_collect
import os
from pathlib import Path
import uuid
import psycopg
from psycopg.rows import dict_row
from psycopg import sql


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


def due_row(stable_key="traffic-1", *, budget_state=None, next_check_at=None, last_checked_at=None):
    return {
        "source_id": "S-004",
        "stable_key": stable_key,
        "requested_url": "https://www.tccc.gov.tw/detail",
        "last_checked_at": last_checked_at,
        "next_check_at": next_check_at or datetime(2026, 9, 21, 11, 0, tzinfo=timezone.utc),
        "etag": None,
        "last_modified": None,
        "document_version_id": None,
        "body_sha256": None,
        "normalized_text_sha256": None,
        "attachments": [],
        "budget_state": {} if budget_state is None else budget_state,
    }


SET_CLAUSE = re.compile(r"SET\s+(.*?)\s+WHERE", re.DOTALL | re.IGNORECASE)


def budget_updates(connection):
    return [
        _assigned_columns(statement, params)
        for statement, params in connection.sql
        if "GREATEST(next_check_at, COALESCE(%s, next_check_at))" in statement
    ]


def full_state_updates(connection):
    return [
        _assigned_columns(statement, params)
        for statement, params in connection.sql
        if statement.lstrip().startswith("UPDATE detail_recheck_state") and "document_version_id = %s" in statement
    ]


def _set_assignments(statement):
    match = SET_CLAUSE.search(statement)
    assert match, statement
    parts, depth, current = [], 0, ""
    for char in match.group(1):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def _assigned_columns(statement, params):
    """Bind each stored value to its SQL column instead of a positional index."""
    assignments = _set_assignments(statement)
    where = statement[SET_CLAUSE.search(statement).end(1) :].strip()
    assert where.upper().startswith("WHERE"), where
    predicates = [
        part.strip() for part in re.split(r"\bAND\b", where[5:], flags=re.IGNORECASE) if part.strip() and "%s" in part
    ]
    assert len(assignments) + len(predicates) == len(params), (assignments, predicates, params)
    bound = {}
    for assignment, value in zip(assignments, params):
        column, _, expression = assignment.partition("=")
        assert expression.count("%s") == 1, assignment
        bound[column.strip()] = value
    for predicate, value in zip(predicates, params[len(assignments) :]):
        column = predicate.partition("=")[0].strip()
        assert column in {"source_id", "stable_key"}, predicate
        bound[column] = value
    return bound


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
        update = full_state_updates(connection)[0]
        self.assertEqual(update["status"], "BASELINE")
        self.assertEqual(update["last_snapshot_id"], result[0]["snapshot_id"])
        self.assertEqual(json.loads(update["budget_state"])["attempts"], ["2026-09-21T20:00:00+08:00"])

    def test_allowed_request_persists_the_budget_attempt(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(due_row())
        session = FakeSession()
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["status"], "BASELINE")
        self.assertEqual(len(session.calls), 1)
        self.assertIsNone(result[0]["reason"])
        budget = json.loads(full_state_updates(connection)[0]["budget_state"])
        self.assertEqual(budget["attempts"], ["2026-09-21T20:00:00+08:00"])
        self.assertEqual(budget["last_decision"], "COMPLETED")
        self.assertEqual(result[0]["budget"]["state"], budget)

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
            skipped["next_check_at"],
            datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc),
            "the retry time must become the next due cursor",
        )
        self.assertEqual(json.loads(skipped["budget_state"])["deferred_until"], "2026-09-21T21:00:00+08:00")

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
        budget = json.loads(full_state_updates(connection)[0]["budget_state"])
        self.assertEqual(budget["attempts"], ["2026-09-21T20:00:00+08:00"])

    def test_per_run_limit_holds_back_the_remaining_due_targets(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection([due_row("traffic-1"), due_row("traffic-2"), due_row("traffic-3")])
        session = FakeSession()
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        candidate_limit = next(
            params[-1]
            for statement, params in connection.sql
            if "FROM detail_recheck_state" in statement and "LIMIT %s" in statement
        )
        self.assertEqual(
            candidate_limit,
            5,
            "the candidate batch must be wider than the per-run ceiling or the caps have nothing to arbitrate",
        )
        self.assertEqual(
            [item["reason"] for item in result],
            [None, "RUN_LIMIT_REACHED", "RUN_LIMIT_REACHED"],
            "the default per-run ceiling of one request must not depend on the SQL LIMIT alone",
        )
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            len(budget_updates(connection)),
            2,
            "each held-back target must still record why it was not requested",
        )
        for update in budget_updates(connection):
            self.assertIsNone(update["next_check_at"], "a run-limit refusal keeps the existing due cursor")

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
        self.assertTrue(json.loads(budget_updates(connection)[0]["budget_state"])["retry_deadline_exceeded"])

    def test_malformed_stored_ledger_blocks_one_row_without_aborting_the_run(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(
            [due_row("traffic-1", budget_state={"attempts": "oops"}), due_row("traffic-2")]
        )
        session = FakeSession()
        result = online_collect.run_detail_rechecks(
            connection, session, {"S-004": "SR-1"}, observed, limit=2
        )
        self.assertEqual([item["reason"] for item in result], ["INVALID_BUDGET_STATE", None])
        self.assertEqual([item["status"] for item in result], ["SKIPPED", "BASELINE"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(json.loads(budget_updates(connection)[0]["budget_state"])["attempts"], [])

    def test_target_selected_before_its_ttl_elapsed_is_not_recorded_as_checked(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        row = due_row(last_checked_at=datetime(2026, 9, 21, 11, 55, tzinfo=timezone.utc))
        row.update(
            {
                "document_version_id": "DOCV-OLD",
                "body_sha256": "a" * 64,
                "normalized_text_sha256": "b" * 64,
            }
        )
        connection = FakeConnection(row)
        result = online_collect.run_detail_rechecks(connection, FakeSession(), {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["status"], "SKIPPED")
        self.assertEqual(result[0]["reason"], "NOT_DUE")
        self.assertEqual(
            full_state_updates(connection),
            [],
            "a target that was never requested must not persist any document state",
        )
        self.assertEqual(
            result[0]["budget"]["state"]["attempts"],
            [],
            "no request means no consumed attempt",
        )
        self.assertEqual(
            budget_updates(connection)[0]["next_check_at"],
            datetime(2026, 9, 22, 11, 55, tzinfo=timezone.utc),
        )

    def test_target_not_yet_due_does_not_consume_the_single_run_slot(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        stale = due_row("traffic-1", last_checked_at=datetime(2026, 9, 21, 11, 55, tzinfo=timezone.utc))
        stale.update({"document_version_id": "DOCV-OLD", "body_sha256": "a" * 64, "normalized_text_sha256": "b" * 64})
        connection = FakeConnection([stale, due_row("traffic-2")])
        session = FakeSession()
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual([item["reason"] for item in result], ["NOT_DUE", None])
        self.assertEqual(len(session.calls), 1, "a not-due target must not starve the run of its only request")
        self.assertEqual(result[1]["status"], "BASELINE")

    def test_429_persists_backoff_instead_of_re_crawling(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        connection = FakeConnection(due_row())
        session = FakeSession(FakeResponse(429, headers={"Retry-After": "120"}))
        result = online_collect.run_detail_rechecks(connection, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(result[0]["status"], "DEFERRED")
        self.assertFalse(result[0]["review_required"])
        update = full_state_updates(connection)[0]
        budget = json.loads(update["budget_state"])
        self.assertEqual(budget["failure_streak"], 1)
        self.assertEqual(budget["deferred_until"], "2026-09-21T20:15:00+08:00")
        self.assertEqual(budget["deadline_at"], "2026-09-22T20:00:00+08:00")
        self.assertEqual(budget["last_decision"], "BACKED_OFF")
        self.assertFalse(budget["retry_deadline_exceeded"])
        self.assertEqual(
            update["next_check_at"],
            datetime(2026, 9, 21, 12, 15, tzinfo=timezone.utc),
            "the backoff window must become the next due cursor so a 429 cannot become a re-crawl loop",
        )
        self.assertIsNone(update["last_snapshot_id"], "a deferred response has no body snapshot")
        self.assertIsNone(result[0]["snapshot_id"])

    def test_not_due_ledger_survives_the_next_due_cycle(self):
        observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
        stale = due_row("traffic-1", last_checked_at=datetime(2026, 9, 21, 11, 55, tzinfo=timezone.utc))
        stale.update({"document_version_id": "DOCV-OLD", "body_sha256": "a" * 64, "normalized_text_sha256": "b" * 64})
        first = FakeConnection(stale)
        online_collect.run_detail_rechecks(first, FakeSession(), {"S-004": "SR-1"}, observed)
        stored = json.loads(budget_updates(first)[0]["budget_state"])
        self.assertEqual(stored["last_decision"], "NOT_DUE")

        second = FakeConnection(due_row("traffic-1", budget_state=stored))
        session = FakeSession()
        result = online_collect.run_detail_rechecks(second, session, {"S-004": "SR-1"}, observed)
        self.assertEqual(
            result[0]["reason"],
            None,
            "a ledger written by a not-due round must be readable again on the next due round",
        )
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(json.loads(full_state_updates(second)[0]["budget_state"])["last_decision"], "COMPLETED")

    def test_budget_host_falls_back_when_the_catalog_cannot_be_read(self):
        row = due_row()
        original = online_collect.validate_document_url
        online_collect.validate_document_url = lambda *args, **kwargs: (_ for _ in ()).throw(
            FileNotFoundError("source-catalog.json")
        )
        try:
            self.assertEqual(online_collect._detail_budget_host(row), "www.tccc.gov.tw")
            row["requested_url"] = "not-a-url"
            self.assertEqual(online_collect._detail_budget_host(row), "unresolved:S-004")
        finally:
            online_collect.validate_document_url = original

    def test_invalid_budget_policy_is_rejected_before_any_state_is_touched(self):
        connection = FakeConnection(due_row())
        with self.assertRaisesRegex(ValueError, "per_source_limit"):
            online_collect.run_detail_rechecks(
                connection,
                FakeSession(),
                {"S-004": "SR-1"},
                datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
                budget_policy={"per_source_limit": 0},
            )
        self.assertEqual(connection.sql, [])

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


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgreSQLRollingBudgetTests(unittest.TestCase):
    """Actual JSONB and transaction checks; transport remains a bounded fixture."""

    def setUp(self):
        self.schema = "govintel_budget_" + uuid.uuid4().hex
        self.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        for migration in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")):
            self.connection.execute(migration.read_text())
        for source_id in ("S-004", "S-006"):
            self.connection.execute("INSERT INTO sources(source_id,name,evidence_role,product_role,integration_status) VALUES (%s,'FICTIONAL TEST SOURCE','PRIMARY_OFFICIAL','PREP_CORE','FIXTURE_ONLY')", (source_id,))
        self.observed = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.connection.rollback()
        self.connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.commit()
        self.connection.close()

    def insert_target(self, source_id, key, ledger, *, future=False):
        self.connection.execute("INSERT INTO detail_recheck_state(source_id,stable_key,requested_url,next_check_at,budget_state) VALUES (%s,%s,'https://www.tccc.gov.tw/detail',%s,%s::jsonb)",
            (source_id, key, datetime(2026, 9, 22 if future else 20, 12, 0, tzinfo=timezone.utc), json.dumps(ledger)))

    def check_blocked(self, reason, policy=None):
        self.connection.commit()
        session = FakeSession()
        result = online_collect.run_detail_rechecks(self.connection, session, {"S-004": "SR-FICTIONAL"}, self.observed, budget_policy=policy)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["reason"], reason)
        self.assertEqual(session.calls, [])
        stored = self.connection.execute("SELECT budget_state, next_check_at FROM detail_recheck_state WHERE stable_key='new-target'").fetchone()
        self.assertEqual(stored["budget_state"]["last_decision"], reason)
        self.assertGreater(stored["next_check_at"], self.observed)
        self.assertEqual(stored["budget_state"]["attempts"], [])

    def test_recent_attempts_on_not_due_targets_block_another_run(self):
        self.insert_target("S-004", "previous-run", {"attempts": ["2026-09-21T11:30:00+00:00", "2026-09-21T11:40:00+00:00", "2026-09-21T11:50:00+00:00"]}, future=True)
        self.insert_target("S-004", "new-target", {})
        self.check_blocked("SOURCE_BUDGET_EXHAUSTED")

    def test_other_source_on_same_official_host_uses_shared_window(self):
        self.insert_target("S-006", "other-source", {"attempts": ["2026-09-21T11:30:00+00:00", "2026-09-21T11:40:00+00:00", "2026-09-21T11:50:00+00:00"]}, future=True)
        self.insert_target("S-004", "new-target", {})
        self.check_blocked("HOST_BUDGET_EXHAUSTED", {"per_host_limit": 3})

    def test_unreadable_historical_ledger_refuses_new_requests(self):
        self.insert_target("S-004", "unknown-history", {"attempts": "invalid"}, future=True)
        self.insert_target("S-004", "new-target", {})
        self.check_blocked("SOURCE_BUDGET_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
