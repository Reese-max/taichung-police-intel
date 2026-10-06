#!/usr/bin/env python3
"""Query or acknowledge the bounded detail-recheck request budget ledger.

The ledger itself lives in ``detail_recheck_state.budget_state`` and is written
by the collector.  This command is the only supported way to read the pending
retry state or to clear a blocked ledger: a hand-edited JSONB value can be
rejected by the collector, so recovery must go through
``acknowledge_recheck_budget``.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collect import TZ, timestamp
from intel_v2.detail_recheck_budget import (
    acknowledge_recheck_budget,
    budget_state_for_storage,
    pending_retry_rows,
    recheck_budget_policy,
)


PENDING_SQL = """
    SELECT source_id, stable_key, requested_url, budget_state
    FROM detail_recheck_state
    WHERE budget_state->>'deferred_until' IS NOT NULL
       OR budget_state->>'deadline_at' IS NOT NULL
    ORDER BY source_id, stable_key
"""
ACK_SQL = """
    UPDATE detail_recheck_state
    SET budget_state = %s::jsonb,
        next_check_at = LEAST(next_check_at, %s),
        updated_at = %s
    WHERE source_id = %s AND stable_key = %s
    RETURNING source_id, stable_key, budget_state, next_check_at
"""


def now() -> str:
    return timestamp(datetime.now(TZ))


def connect():
    import psycopg
    from psycopg.rows import dict_row

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg.connect(database_url, row_factory=dict_row)


def rows_for(states: list[dict], observed_at: str) -> list[dict]:
    return pending_retry_rows(
        [
            {
                "source_id": row["source_id"],
                "stable_key": row["stable_key"],
                "host": budget_host(row),
                "budget_state": row.get("budget_state"),
            }
            for row in states
        ],
        now=observed_at,
    )


def budget_host(row: dict) -> str:
    """Resolve the approved host for a stored target with the collector's rule."""
    if row.get("host"):
        return str(row["host"])
    from online_collect import _detail_budget_host

    return _detail_budget_host({"source_id": row["source_id"], "requested_url": row.get("requested_url")})


def command_list(args: argparse.Namespace) -> int:
    observed_at = args.now or now()
    with connect() as connection:
        states = connection.execute(PENDING_SQL).fetchall()
    print(json.dumps({"observed_at": observed_at, "pending": rows_for(states, observed_at)}, ensure_ascii=False, sort_keys=True))
    return 0


def command_acknowledge(args: argparse.Namespace) -> int:
    observed_at = args.now or now()
    policy = recheck_budget_policy(json.loads(args.policy) if args.policy else None)
    with connect() as connection:
        row = connection.execute(
            "SELECT budget_state FROM detail_recheck_state WHERE source_id = %s AND stable_key = %s",
            (args.source_id, args.stable_key),
        ).fetchone()
        if not row:
            raise SystemExit("detail recheck target is not registered")
        cleared = acknowledge_recheck_budget(row["budget_state"], now=observed_at, policy=policy)
        due = datetime.now(TZ)
        updated = connection.execute(
            ACK_SQL,
            (
                json.dumps(budget_state_for_storage(cleared, now=observed_at, policy=policy), ensure_ascii=False, sort_keys=True),
                due,
                due,
                args.source_id,
                args.stable_key,
            ),
        ).fetchone()
    print(
        json.dumps(
            {
                "acknowledged_at": observed_at,
                "target": updated["source_id"] + ":" + updated["stable_key"],
                "next_check_at": timestamp(updated["next_check_at"]),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def self_check() -> None:
    columns = re.findall(r"(\w+)\s*=\s*[^,]+,", ACK_SQL)
    assert columns == ["budget_state", "next_check_at", "updated_at"], columns
    assert ACK_SQL.count("%s") == 5, ACK_SQL.count("%s")
    assert "budget_state->>'deferred_until' IS NOT NULL" in PENDING_SQL, PENDING_SQL
    observed_at = "2026-09-21T12:00:00+08:00"
    blocked = {
        "attempts": [observed_at],
        "failure_streak": 4,
        "first_deferred_at": "2026-09-20T12:00:00+08:00",
        "deadline_at": observed_at,
        "deferred_until": "2026-09-21T11:00:00+08:00",
    }
    rows = rows_for(
        [
            {
                "source_id": "S-004",
                "stable_key": "traffic-1",
                "requested_url": "https://www.tccc.gov.tw/detail",
                "budget_state": blocked,
            },
            {"source_id": "S-004", "stable_key": "traffic-2", "requested_url": "https://www.tccc.gov.tw/detail", "budget_state": {}},
        ],
        observed_at,
    )
    assert len(rows) == 1, rows
    assert rows[0]["stable_key"] == "traffic-1" and rows[0]["retry_deadline_exceeded"] is True, rows
    assert rows[0]["host"] == "www.tccc.gov.tw", rows
    cleared = acknowledge_recheck_budget(blocked, now=observed_at)
    assert cleared["retry_deadline_exceeded"] is False and cleared["deferred_until"] is None, cleared
    assert rows_for([{"source_id": "S-004", "stable_key": "traffic-1", "requested_url": "https://www.tccc.gov.tw/detail", "budget_state": cleared}], observed_at) == []
    print("DETAIL_RECHECK_BUDGET_SELF_CHECK_OK pending=1 acknowledged=1 host=resolved")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--now", help="ISO-8601 timestamp override for deterministic output")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="print the pending retry ledger").set_defaults(handler=command_list)
    acknowledge = sub.add_parser("acknowledge", help="clear a blocked ledger after a human decision")
    acknowledge.add_argument("--source-id", required=True)
    acknowledge.add_argument("--stable-key", required=True)
    acknowledge.add_argument("--policy", help="JSON budget policy override")
    acknowledge.set_defaults(handler=command_acknowledge)
    return result


def main(argv: list[str] | None = None) -> int:
    if argv is None and "--self-check" in sys.argv:
        self_check()
        return 0
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
        raise SystemExit(0)
    raise SystemExit(main())
