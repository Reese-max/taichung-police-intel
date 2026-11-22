#!/usr/bin/env python3
"""Recover missed national sync schedules without retrying indefinitely."""

import json
import os
import subprocess
import sys
from datetime import datetime, time, timezone


UTC = timezone.utc
# GitHub fires this repo's schedules 7-8 h late (2026-11-20..22: 00:20 xisx ran ~07:00,
# 02:17 hosted ~09:45), so deadlines sit past that delay to avoid duplicate dispatches.
WATCHED_WORKFLOWS = {
    "national-sync-xisx.yml": time(12, 0),
    "national-sync.yml": time(15, 0),
}


def _created_at_utc(value):
    if not isinstance(value, str):
        return None
    try:
        created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(UTC)


def decide(now_utc, runs, latest_start_utc):
    """Return OK, WAIT, DISPATCH, or ALERT for one workflow and UTC day."""
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")

    now = now_utc.astimezone(UTC)
    today = now.date()
    watchdog_dispatched = False

    for run in runs:
        created_at = _created_at_utc(run.get("created_at"))
        if created_at is None or created_at.date() != today:
            continue

        status = str(run.get("status") or "").lower()
        conclusion = str(run.get("conclusion") or "").lower()
        if status in {"queued", "in_progress"} or conclusion == "success":
            return "OK"

        actor = run.get("triggering_actor")
        actor_login = actor.get("login") if isinstance(actor, dict) else None
        if run.get("event") == "workflow_dispatch" and actor_login == "github-actions[bot]":
            watchdog_dispatched = True

    deadline = datetime.combine(today, latest_start_utc, tzinfo=UTC)
    if now < deadline:
        return "WAIT"
    return "ALERT" if watchdog_dispatched else "DISPATCH"


def fetch_runs(repository, workflow_file, day):
    result = subprocess.run(
        [
            "gh", "api", "--paginate", "--slurp", "--method", "GET",
            f"repos/{repository}/actions/workflows/{workflow_file}/runs",
            "-f", f"created=>={day.isoformat()}",
            "-f", "per_page=100",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    pages = json.loads(result.stdout)
    if not isinstance(pages, list) or any(not isinstance(page, dict) for page in pages):
        raise ValueError("gh api returned an unexpected workflow-runs response")
    return [run for page in pages for run in page.get("workflow_runs", [])]


def dispatch_workflow(repository, workflow_file):
    subprocess.run(
        ["gh", "workflow", "run", workflow_file, "--ref", "main", "--repo", repository],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def main():
    if not os.environ.get("GH_TOKEN"):
        print("::error::GH_TOKEN is required", file=sys.stderr)
        return 2
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        print("::error::GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    failed = False
    for workflow_file, latest_start in WATCHED_WORKFLOWS.items():
        try:
            runs = fetch_runs(repository, workflow_file, now.date())
            result = decide(now, runs, latest_start)
            if result == "DISPATCH":
                dispatch_workflow(repository, workflow_file)
            elif result == "ALERT":
                print(f"::warning::{workflow_file} was already dispatched today but is still failed or absent")
            print(f"::notice::{workflow_file}: {result}")
        except subprocess.CalledProcessError as exc:
            print(f"::error::{workflow_file}: gh command failed with exit code {exc.returncode}", file=sys.stderr)
            failed = True
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"::error::{workflow_file}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
