from __future__ import annotations

import copy
import unittest

from intel_v2.live_meeting import (
    activate_session,
    add_bookmark,
    append_segment,
    budget_stop,
    formal_candidates,
    reconcile_session,
    record_gap,
    resume_session,
    start_session,
    stop_session,
    validate_session,
)


T0 = "2026-09-21T10:00:00+08:00"
T1 = "2026-09-21T10:01:00+08:00"
SOURCE_URL = "https://www.tccc.gov.tw/"
STREAM_URL = "https://vod.tccc.gov.tw/live/demo.m3u8"


def started(*, authorized: bool = True, transport: bool = True, budget: int | None = 60):
    return start_session(
        source_id="S-010",
        official_source_url=SOURCE_URL,
        stream_url=STREAM_URL,
        started_at=T0,
        meeting_id="MEETING-DEMO",
        agenda_id="AGENDA-DEMO",
        max_duration_seconds=60,
        asr_contract={"provider": "fixture", "model": "provisional", "version": "v1", "language": "zh"},
        transport_enabled=transport,
        provider_authorized=authorized,
        budget_seconds=budget,
        session_id="LM-DEMO",
    )


class LiveMeetingTests(unittest.TestCase):
    def test_explicit_transport_is_bounded_and_no_auth_stays_preparing(self):
        state = started(authorized=False)
        self.assertEqual(state["status"], "PREPARING")
        self.assertEqual(state["transport_status"], "BLOCKED_NO_AUTH")
        with self.assertRaisesRegex(ValueError, "authorization"):
            activate_session(state, activated_at=T1, provider_authorized=False, budget_seconds=30)

    def test_interim_final_revision_keeps_one_stable_segment(self):
        state = started()
        state = append_segment(
            state,
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="交通議題正在討論",
            received_at=T0,
            partial=True,
            speaker_label="SPEAKER_1",
        )
        state = append_segment(
            state,
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="交通議題已進入討論",
            received_at=T1,
            finalized=True,
            speaker_label="SPEAKER_1",
        )
        self.assertEqual(len(state["segments"]), 1)
        self.assertEqual(state["segments"][0]["revision"], 2)
        self.assertEqual(len(state["segments"][0]["revision_history"]), 1)
        self.assertNotEqual(
            state["segments"][0]["content_sha256"],
            state["segments"][0]["revision_history"][0]["content_sha256"],
        )
        validate_session(state)

    def test_gap_reconnect_and_bookmark_preserve_navigation_state(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="第一段",
            received_at=T0,
            finalized=True,
        )
        state = record_gap(state, start_seconds=5, end_seconds=10, reason="STREAM_DISCONNECT", detected_at=T1)
        self.assertEqual(state["status"], "DEGRADED")
        state = resume_session(state, resumed_at=T1)
        state = append_segment(
            state,
            sequence=1,
            start_seconds=10,
            end_seconds=15,
            text="重新連線後的第二段",
            received_at=T1,
            finalized=True,
        )
        state = add_bookmark(
            state,
            segment_ids=[state["segments"][0]["segment_id"]],
            reason_code="TOPIC_MATCH",
            bookmarked_at=T1,
            note="交通議題回看",
        )
        self.assertEqual(len(state["gap_intervals"]), 1)
        self.assertEqual(len(state["bookmarks"]), 1)
        self.assertEqual(state["bookmarks"][0]["stream_content_hash"], state["stream_content_hash"])
        validate_session(state)

    def test_budget_stop_records_gap_instead_of_silent_no_content(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="已收到內容",
            received_at=T0,
            finalized=True,
        )
        state = budget_stop(state, stopped_at=T1)
        self.assertEqual(state["status"], "DEGRADED")
        self.assertEqual(state["transport_status"], "BUDGET_STOP")
        self.assertEqual(state["gap_intervals"][0]["reason"], "BUDGET_STOP")
        self.assertEqual(state["gap_intervals"][0]["end_seconds"], 60.0)

    def test_reconciliation_binds_official_locator_and_excludes_provisional(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="暫定文字",
            received_at=T0,
            finalized=True,
        )
        state = add_bookmark(
            state,
            segment_ids=[state["segments"][0]["segment_id"]],
            reason_code="REVIEW",
            bookmarked_at=T0,
        )
        state = stop_session(state, ended_at=T1)
        bookmark_id = state["bookmarks"][0]["bookmark_id"]
        state = reconcile_session(
            state,
            [
                {
                    "candidate_id": bookmark_id,
                    "outcome": "CONFIRMED_BY_OFFICIAL_MEDIA",
                    "official_source_id": "S-010",
                    "official_url": "https://vod.tccc.gov.tw/wb_news02.asp?url=92",
                    "official_locator": "timestamp:0-5",
                    "official_document_version": "VOD-1",
                    "official_text_sha256": "a" * 64,
                }
            ],
            reconciled_at=T1,
        )
        self.assertEqual(state["status"], "RECONCILED")
        candidates = formal_candidates(state)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["provisional"])
        self.assertEqual(candidates[0]["verification_status"], "OFFICIAL_RECONCILED")
        self.assertNotEqual(candidates[0]["verification_status"], "AUTO_PASS")
        self.assertEqual(candidates[0]["official"]["locator"], "timestamp:0-5")

    def test_pending_source_and_later_contradiction_keep_receipt_history(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="可能的決議",
            received_at=T0,
            finalized=True,
        )
        state = add_bookmark(state, segment_ids=[state["segments"][0]["segment_id"]], reason_code="REVIEW", bookmarked_at=T0)
        state = stop_session(state, ended_at=T1)
        bookmark_id = state["bookmarks"][0]["bookmark_id"]
        state = reconcile_session(
            state,
            [{"candidate_id": bookmark_id, "outcome": "SOURCE_NOT_YET_AVAILABLE"}],
            reconciled_at=T1,
        )
        self.assertEqual(state["status"], "RECONCILING")
        state = reconcile_session(
            state,
            [{"candidate_id": bookmark_id, "outcome": "SUPERSEDED_TRANSCRIPT"}],
            reconciled_at=T1,
        )
        self.assertEqual(state["status"], "RECONCILED")
        self.assertEqual(len(state["reconciliation_receipts"]), 2)
        self.assertEqual(formal_candidates(state), [])

    def test_sensitive_speaker_and_arbitrary_stream_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            start_session(
                source_id="S-010",
                official_source_url=SOURCE_URL,
                stream_url="https://evil.example/live.m3u8",
                started_at=T0,
                asr_contract={"provider": "fixture", "model": "m", "version": "v1", "language": "zh"},
            )
        with self.assertRaisesRegex(ValueError, "unverified speaker"):
            append_segment(
                started(),
                sequence=0,
                start_seconds=0,
                end_seconds=1,
                text="公開文字",
                received_at=T0,
                speaker_label="局長王小明",
            )

    def test_tampered_session_hash_fails_closed(self):
        state = started()
        tampered = copy.deepcopy(state)
        tampered["stream_url"] = "https://evil.example/live.m3u8"
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            validate_session(tampered)


if __name__ == "__main__":
    unittest.main()
