"""Replay the issue-13 live-meeting scenario fixtures against the state machine.

Each JSON fixture under ``tests/fixtures/live-meeting/scenario-*.json`` is an
ordered op log plus expectations. Replaying a fixture must produce the recorded
session status, gap visibility, revision history, reconciliation outcomes, and
the guarantee that provisional text never becomes a formal fact on its own.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from intel_v2.live_meeting import (
    activate_session,
    add_bookmark,
    append_segment,
    budget_stop,
    build_timeline,
    fail_session,
    formal_candidates,
    mark_receipt_stale,
    reconcile_session,
    record_gap,
    resume_session,
    search_segments,
    segment_locator,
    start_session,
    stop_session,
    validate_session,
)
from intel_v2.role_profiles import load_catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "live-meeting"
PROFILE_CATALOG = ROOT / "docs" / "govintel" / "role-profiles.v1.json"


def _resolve_ref(state: dict, ctx: dict, value):
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"\$(seg|bookmark|receipt):(\d+)", value)
    if not match:
        return value
    kind, index = match.group(1), int(match.group(2))
    if kind == "seg":
        return ctx["segments"][index]["segment_id"]
    if kind == "bookmark":
        return ctx["bookmarks"][index]["bookmark_id"]
    return ctx["receipts"][index]["receipt_id"]


def _profile(ctx: dict, profile_id: str | None):
    if profile_id is None:
        return None
    catalog = ctx.setdefault("catalog", load_catalog(PROFILE_CATALOG))
    return catalog["profiles"][profile_id]


def _apply(state: dict, op: dict, ctx: dict):
    name = op["op"]
    at = op.get("at")
    if name == "append_segment":
        return append_segment(
            state,
            sequence=op["sequence"],
            start_seconds=op["start_seconds"],
            end_seconds=op["end_seconds"],
            text=op["text"],
            received_at=at,
            finalized=op.get("finalized", False),
            partial=op.get("partial", False),
            confidence=op.get("confidence"),
            speaker_label=op.get("speaker_label"),
            profile=_profile(ctx, op.get("profile_id")),
        ), state
    if name == "record_gap":
        return record_gap(state, start_seconds=op["start_seconds"], end_seconds=op["end_seconds"], reason=op["reason"], detected_at=at), None
    if name == "resume_session":
        return resume_session(state, resumed_at=at), None
    if name == "stop_session":
        return stop_session(state, ended_at=at, reason=op.get("reason", "USER_STOP")), None
    if name == "budget_stop":
        return budget_stop(state, stopped_at=at), None
    if name == "fail_session":
        return fail_session(state, failed_at=at, reason=op["reason"]), None
    if name == "serialize_roundtrip":
        return json.loads(json.dumps(state, ensure_ascii=False)), None
    if name == "bookmark":
        segment_ids = [_resolve_ref(state, ctx, item) for item in op["segment_ids"]]
        return add_bookmark(state, segment_ids=segment_ids, reason_code=op["reason_code"], bookmarked_at=at, note=op.get("note")), None
    if name == "reconcile":
        candidates = []
        for candidate in op["candidates"]:
            entry = dict(candidate)
            for key in ("candidate_id", "bookmark_id"):
                if key in entry:
                    entry[key] = _resolve_ref(state, ctx, entry[key])
            if "segment_ids" in entry:
                entry["segment_ids"] = [_resolve_ref(state, ctx, item) for item in entry["segment_ids"]]
            candidates.append(entry)
        return reconcile_session(state, candidates, reconciled_at=at), None
    if name == "mark_receipt_stale":
        receipt_id = _resolve_ref(state, ctx, op["receipt_id"])
        return mark_receipt_stale(state, receipt_id=receipt_id, observed_official_sha256=op["observed_official_sha256"], marked_at=at), None
    raise AssertionError(f"unsupported op: {name}")


def _query(state: dict, op: dict, ctx: dict):
    name = op["op"]
    if name == "search":
        return search_segments(state, op["query"], limit=op.get("limit", 50))
    if name == "locator":
        return segment_locator(state, _resolve_ref(state, ctx, op["segment_id"]))
    if name == "timeline":
        return build_timeline(state)
    if name == "formal_candidates":
        return formal_candidates(state)
    raise AssertionError(f"unsupported query op: {name}")


def _check_expect(test: unittest.TestCase, expect: dict, state: dict, output, ctx: dict) -> None:
    for key, want in expect.items():
        if key == "status":
            test.assertEqual(state["status"], want)
        elif key == "transport_status":
            test.assertEqual(state["transport_status"], want)
        elif key == "segment_count":
            test.assertEqual(len(state["segments"]), want)
        elif key == "gap_count":
            test.assertEqual(len(state["gap_intervals"]), want)
        elif key == "bookmark_count":
            test.assertEqual(len(state["bookmarks"]), want)
        elif key == "receipt_count":
            test.assertEqual(len(state["reconciliation_receipts"]), want)
        elif key in {"revision", "revision_history_len"}:
            seq = expect.get("sequence", ctx.get("last_sequence"))
            if seq is None:
                raise AssertionError(f"{key} expectation has no target sequence")
            segment = next(item for item in state["segments"] if item["sequence"] == seq)
            if key == "revision":
                test.assertEqual(segment["revision"], want)
            else:
                test.assertEqual(len(segment["revision_history"]), want)
        elif key == "stale_count":
            test.assertEqual(sum(1 for r in state["reconciliation_receipts"] if r.get("stale")), want)
        elif key == "superseded_count":
            test.assertEqual(sum(1 for r in state["reconciliation_receipts"] if r.get("superseded_by")), want)
        elif key == "formal_count":
            test.assertEqual(len(formal_candidates(state)), want)
        elif key == "formal_verification_statuses":
            test.assertEqual([item["verification_status"] for item in formal_candidates(state)], want)
        elif key == "match_count":
            test.assertEqual(output["match_count"], want)
        elif key == "all_provisional":
            test.assertTrue(output["provisional"])
            test.assertTrue(all(match["provisional"] for match in output["matches"]))
        elif key == "truncated":
            test.assertEqual(output["truncated"], want)
        elif key == "locator_kind":
            test.assertEqual(output["kind"], want)
        elif key == "locator_reliable":
            test.assertEqual(output["reliable_seek"], want)
        elif key == "locator_url_null":
            test.assertIs(output["url"] is None, want)
        elif key == "timeline_kinds":
            test.assertEqual([entry["kind"] for entry in output], want)
        elif key == "timeline_all_visible":
            test.assertTrue(all(entry["display_empty"] is False for entry in output))
        elif key == "timeline_all_provisional":
            test.assertTrue(all(entry["provisional"] for entry in output))
        else:
            raise AssertionError(f"unsupported expectation: {key}")


class LiveMeetingScenarioTests(unittest.TestCase):
    def test_scenario_fixtures_replay(self):
        fixtures = sorted(FIXTURE_DIR.glob("scenario-*.json"))
        self.assertGreaterEqual(len(fixtures), 10)
        for fixture_path in fixtures:
            with self.subTest(fixture=fixture_path.name):
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                session_cfg = dict(fixture["session"])
                session_cfg.setdefault("asr_contract", {"provider": "fixture", "model": "provisional", "version": "v1", "language": "zh"})
                state = start_session(**session_cfg)
                ctx: dict = {"segments": [], "bookmarks": [], "receipts": []}
                for op in fixture["ops"]:
                    if "sequence" in op:
                        ctx["last_sequence"] = op["sequence"]
                    expect_error = op.get("expect_error")
                    if expect_error:
                        if "expect" in op:
                            raise AssertionError("expect cannot be combined with expect_error")
                        with self.assertRaisesRegex(ValueError, expect_error):
                            _apply(state, op, ctx)
                        continue
                    if op["op"] in {"search", "locator", "timeline", "formal_candidates"}:
                        if "expect" not in op:
                            raise AssertionError(f"query op {op['op']} must declare expect")
                        output = _query(state, op, ctx)
                        _check_expect(self, op["expect"], state, output, ctx)
                        continue
                    state, _ = _apply(state, op, ctx)
                    ctx["segments"] = list(state["segments"])
                    ctx["bookmarks"] = list(state["bookmarks"])
                    ctx["receipts"] = list(state["reconciliation_receipts"])
                    validate_session(state)
                    if "expect" in op:
                        _check_expect(self, op["expect"], state, None, ctx)
                if "expect" in fixture:
                    _check_expect(self, fixture["expect"], state, None, ctx)

    def test_canonical_session_fixtures_validate(self):
        # session-live.json / session-reconciled.json are shared with the web
        # view contract tests; validating them here keeps both stacks pinned to
        # the same hash-bound schema.
        for name in ("session-live.json", "session-reconciled.json"):
            with self.subTest(fixture=name):
                state = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
                validate_session(state)


if __name__ == "__main__":
    unittest.main()
