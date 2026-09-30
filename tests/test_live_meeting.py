from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from intel_v2.live_meeting import (
    activate_session,
    add_bookmark,
    append_segment,
    budget_stop,
    build_timeline,
    fail_session,
    formal_candidates,
    highlight_profile,
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


T0 = "2026-09-21T10:00:00+08:00"
T1 = "2026-09-21T10:01:00+08:00"
SOURCE_URL = "https://www.tccc.gov.tw/"
STREAM_URL = "https://vod.tccc.gov.tw/live/demo.m3u8"
PROFILE = load_catalog(Path(__file__).resolve().parents[1] / "docs" / "govintel" / "role-profiles.v1.json")["profiles"]["traffic-policy"]


def started(*, authorized: bool = True, transport: bool = True, budget: int | None = 60, max_duration: int = 60):
    return start_session(
        source_id="S-010",
        official_source_url=SOURCE_URL,
        stream_url=STREAM_URL,
        started_at=T0,
        meeting_id="MEETING-DEMO",
        agenda_id="AGENDA-DEMO",
        max_duration_seconds=max_duration,
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

    def test_profile_highlight_is_deterministic_and_bookmark_bound(self):
        text = "交通與道路安全議題進入討論"
        relevance = highlight_profile(text, PROFILE)
        self.assertIn("topic_match", relevance["reason_codes"])
        self.assertIn("交通", relevance["matched_terms"])
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text=text,
            received_at=T0,
            finalized=True,
            profile=PROFILE,
        )
        self.assertEqual(state["segments"][0]["profile_relevance"], relevance)
        state = add_bookmark(
            state,
            segment_ids=[state["segments"][0]["segment_id"]],
            reason_code="TOPIC_MATCH",
            bookmarked_at=T1,
        )
        self.assertEqual(state["bookmarks"][0]["profile_relevance"], relevance)
        with self.assertRaisesRegex(ValueError, "derived from a selected segment"):
            add_bookmark(
                state,
                segment_ids=[state["segments"][0]["segment_id"]],
                reason_code="TOPIC_MATCH",
                bookmarked_at=T1,
                profile_relevance={**relevance, "profile_id": "general"},
            )

    def test_serialized_restart_and_cross_type_overlap_fail_closed(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="可重啟的暫定片段",
            received_at=T0,
            finalized=True,
        )
        restored = json.loads(json.dumps(state, ensure_ascii=False))
        resumed = append_segment(
            restored,
            sequence=1,
            start_seconds=5,
            end_seconds=10,
            text="重啟後的片段",
            received_at=T1,
            finalized=True,
        )
        self.assertEqual(len(resumed["segments"]), 2)
        tampered = copy.deepcopy(state)
        tampered["gap_intervals"].append({
            "gap_id": "GAP-TAMPERED",
            "start_seconds": 4.0,
            "end_seconds": 6.0,
            "reason": "STREAM_DISCONNECT",
            "detected_at": T1,
        })
        with self.assertRaisesRegex(ValueError, "overlaps"):
            validate_session(tampered)


    def test_search_segments_returns_provisional_bounded_matches(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="交通議題正在討論",
            received_at=T0,
            finalized=True,
        )
        state = append_segment(
            state,
            sequence=1,
            start_seconds=5,
            end_seconds=10,
            text="預算安排",
            received_at=T1,
            finalized=True,
        )
        result = search_segments(state, "交通")
        self.assertTrue(result["provisional"])
        self.assertEqual(result["match_count"], 1)
        hit = result["matches"][0]
        self.assertEqual(hit["segment_id"], state["segments"][0]["segment_id"])
        self.assertTrue(hit["provisional"])
        self.assertEqual(hit["locator"]["kind"], "TIME_TEXT")
        self.assertEqual(search_segments(state, "不存在")["match_count"], 0)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            search_segments(state, "  ")
        with self.assertRaisesRegex(ValueError, "limit"):
            search_segments(state, "交通", limit=0)

    def test_segment_locator_degrades_to_time_text_without_seek(self):
        state = append_segment(
            started(max_duration=180, budget=180),
            sequence=0,
            start_seconds=90,
            end_seconds=95,
            text="定位片段",
            received_at=T0,
            finalized=True,
        )
        locator = segment_locator(state, state["segments"][0]["segment_id"])
        self.assertEqual(locator["kind"], "TIME_TEXT")
        self.assertIsNone(locator["url"])
        self.assertFalse(locator["reliable_seek"])
        self.assertTrue(locator["provisional"])
        self.assertEqual(locator["time_seconds"], 90)
        self.assertEqual(locator["display"], "stream+00:01:30")
        with self.assertRaisesRegex(ValueError, "unknown segment"):
            segment_locator(state, "SEG-NOPE")

    def test_segment_locator_uses_stream_time_when_seek_contract_present(self):
        state = start_session(
            source_id="S-010",
            official_source_url=SOURCE_URL,
            stream_url=STREAM_URL,
            started_at=T0,
            max_duration_seconds=60,
            asr_contract={"provider": "fixture", "model": "provisional", "version": "v1", "language": "zh"},
            transport_enabled=True,
            provider_authorized=True,
            budget_seconds=60,
            session_id="LM-SEEK",
            seek={
                "kind": "STREAM_TIME",
                "base_url": "https://vod.tccc.gov.tw/live/demo.m3u8",
                "offset_seconds": 30,
            },
        )
        state = append_segment(
            state,
            sequence=0,
            start_seconds=5,
            end_seconds=9,
            text="可尋址片段",
            received_at=T0,
            finalized=True,
        )
        locator = segment_locator(state, state["segments"][0]["segment_id"])
        self.assertEqual(locator["kind"], "STREAM_TIME")
        self.assertTrue(locator["reliable_seek"])
        self.assertEqual(locator["url"], "https://vod.tccc.gov.tw/live/demo.m3u8")
        self.assertEqual(locator["time_seconds"], 35)
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            start_session(
                source_id="S-010",
                official_source_url=SOURCE_URL,
                stream_url=STREAM_URL,
                started_at=T0,
                asr_contract={"provider": "fixture", "model": "m", "version": "v1", "language": "zh"},
                seek={"kind": "STREAM_TIME", "base_url": "https://evil.example/live", "offset_seconds": 0},
            )

    def test_timeline_marks_gaps_visible_instead_of_empty(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="片段",
            received_at=T0,
            finalized=True,
        )
        state = record_gap(state, start_seconds=5, end_seconds=10, reason="STREAM_DISCONNECT", detected_at=T1)
        timeline = build_timeline(state)
        self.assertEqual([entry["kind"] for entry in timeline], ["SEGMENT", "GAP"])
        self.assertTrue(all(entry["display_empty"] is False for entry in timeline))
        self.assertTrue(all(entry["provisional"] for entry in timeline))
        gap_entry = timeline[1]
        self.assertEqual(gap_entry["gap_id"], state["gap_intervals"][0]["gap_id"])
        self.assertIn("GAP", gap_entry["badges"])

    def test_receipt_records_reconciler_versions(self):
        state = self._reconciled_state()
        receipt = state["reconciliation_receipts"][0]
        self.assertEqual(receipt["reconciler"]["name"], "intel_v2.live_meeting")
        self.assertEqual(receipt["reconciler"]["schema_version"], 1)
        self.assertEqual(receipt["asr_contract"]["model"], "provisional")
        self.assertEqual(receipt["official"]["document_version"], "VOD-1")

    def test_stale_receipt_blocks_formal_candidates_until_rereconciled(self):
        state = self._reconciled_state()
        receipt = state["reconciliation_receipts"][0]
        state = mark_receipt_stale(
            state,
            receipt_id=receipt["receipt_id"],
            observed_official_sha256="b" * 64,
            marked_at="2026-09-21T12:00:00+08:00",
        )
        stale = state["reconciliation_receipts"][0]
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["stale_reason"], "SOURCE_REVISED")
        self.assertEqual(stale["official"]["text_sha256"], "a" * 64)
        self.assertEqual(state["status"], "RECONCILING")
        self.assertEqual(formal_candidates(state), [])
        validate_session(state)
        unchanged = mark_receipt_stale(
            state,
            receipt_id=receipt["receipt_id"],
            observed_official_sha256="c" * 64,
            marked_at="2026-09-21T12:30:00+08:00",
        )
        self.assertEqual(unchanged["reconciliation_receipts"], state["reconciliation_receipts"])
        self.assertEqual(unchanged["session_revision"], state["session_revision"])

    def test_stale_marking_requires_hash_mismatch_and_official_binding(self):
        state = self._reconciled_state()
        receipt = state["reconciliation_receipts"][0]
        unchanged = mark_receipt_stale(
            state,
            receipt_id=receipt["receipt_id"],
            observed_official_sha256="a" * 64,
            marked_at="2026-09-21T12:00:00+08:00",
        )
        self.assertEqual(unchanged["reconciliation_receipts"], state["reconciliation_receipts"])
        with self.assertRaisesRegex(ValueError, "unknown receipt"):
            mark_receipt_stale(
                state,
                receipt_id="RC-NOPE",
                observed_official_sha256="b" * 64,
                marked_at="2026-09-21T12:00:00+08:00",
            )
        pending = self._pending_state()
        pending_receipt = pending["reconciliation_receipts"][0]
        with self.assertRaisesRegex(ValueError, "no official source binding"):
            mark_receipt_stale(
                pending,
                receipt_id=pending_receipt["receipt_id"],
                observed_official_sha256="b" * 64,
                marked_at="2026-09-21T12:00:00+08:00",
            )

    def test_rereconcile_marks_previous_receipt_superseded(self):
        state = self._reconciled_state()
        bookmark_id = state["bookmarks"][0]["bookmark_id"]
        state = reconcile_session(
            state,
            [
                {
                    "candidate_id": bookmark_id,
                    "outcome": "SUPERSEDED_TRANSCRIPT",
                }
            ],
            reconciled_at="2026-09-21T12:00:00+08:00",
        )
        receipts = state["reconciliation_receipts"]
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0]["superseded_by"], receipts[1]["receipt_id"])
        self.assertNotIn("stale", receipts[0])
        self.assertEqual(state["current_reconciliations"][bookmark_id], receipts[1]["receipt_id"])
        self.assertEqual(formal_candidates(state), [])
        validate_session(state)

    def test_fail_session_and_resume_cycle(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="crash 前片段",
            received_at=T0,
            finalized=True,
        )
        state = fail_session(state, failed_at=T1, reason="ASR_TRANSPORT_CRASH")
        self.assertEqual(state["status"], "FAILED")
        self.assertEqual(state["transport_status"], "FAILED")
        with self.assertRaisesRegex(ValueError, "LIVE or DEGRADED"):
            append_segment(
                state,
                sequence=1,
                start_seconds=5,
                end_seconds=10,
                text="失敗期間不可寫入",
                received_at=T1,
            )
        state = resume_session(state, resumed_at=T1)
        self.assertEqual(state["status"], "LIVE")

    def test_fail_and_resume_cannot_bypass_transport_authorization(self):
        preparing = started(authorized=False)
        self.assertEqual(preparing["status"], "PREPARING")
        with self.assertRaisesRegex(ValueError, "active transport"):
            fail_session(preparing, failed_at=T1, reason="nothing to crash")
        failed = json.loads(json.dumps(preparing, ensure_ascii=False))
        failed["status"] = "FAILED"
        failed["transport_status"] = "FAILED"
        failed["budget_seconds"] = None
        import intel_v2.live_meeting as live_meeting
        failed["content_hash"] = live_meeting._full_hash(failed)
        failed["stream_content_hash"] = live_meeting._stream_hash(failed)
        with self.assertRaisesRegex(ValueError, "never activated"):
            resume_session(failed, resumed_at=T1)

    def test_failed_session_can_be_stopped_and_reconciled(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="結束後仍要對帳",
            received_at=T0,
            finalized=True,
        )
        state = add_bookmark(
            state,
            segment_ids=[state["segments"][0]["segment_id"]],
            reason_code="REVIEW",
            bookmarked_at=T0,
        )
        state = fail_session(state, failed_at=T1, reason="ASR_TRANSPORT_CRASH")
        state = stop_session(state, ended_at=T1)
        self.assertEqual(state["status"], "ENDED")
        state = reconcile_session(
            state,
            [{
                "candidate_id": state["bookmarks"][0]["bookmark_id"],
                "outcome": "CONFIRMED_BY_MINUTES",
                "official_source_id": "S-010",
                "official_url": "https://www.tccc.gov.tw/minutes/2026-09-21",
                "official_locator": "minutes:item-1",
                "official_document_version": "MINUTES-1",
                "official_text_sha256": "e" * 64,
            }],
            reconciled_at="2026-09-22T09:00:00+08:00",
        )
        self.assertEqual(state["status"], "RECONCILED")
        self.assertEqual(len(formal_candidates(state)), 1)

    def test_reconcile_without_any_candidate_stays_reconciling(self):
        state = stop_session(append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="沒有任何候選",
            received_at=T0,
            finalized=True,
        ), ended_at=T1)
        state = reconcile_session(state, [], reconciled_at=T1)
        self.assertEqual(state["status"], "RECONCILING")
        self.assertEqual(formal_candidates(state), [])

    def test_fabricated_receipt_fails_validation_even_with_fixed_hashes(self):
        import intel_v2.live_meeting as live_meeting
        state = self._reconciled_state()
        receipt = state["reconciliation_receipts"][0]
        receipt["official"]["official_url"] = "https://evil.example/official"
        receipt["official"]["text_sha256"] = "f" * 64
        receipt["receipt_id"] = "RC-" + "F" * 20
        state["content_hash"] = live_meeting._full_hash(state)
        state["stream_content_hash"] = live_meeting._stream_hash(state)
        with self.assertRaisesRegex(ValueError, "receipt"):
            validate_session(state)

    def _reconciled_state(self):
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
        return reconcile_session(
            state,
            [
                {
                    "candidate_id": state["bookmarks"][0]["bookmark_id"],
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

    def _pending_state(self):
        state = append_segment(
            started(),
            sequence=0,
            start_seconds=0,
            end_seconds=5,
            text="待定片段",
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
        return reconcile_session(
            state,
            [{"candidate_id": state["bookmarks"][0]["bookmark_id"], "outcome": "SOURCE_NOT_YET_AVAILABLE"}],
            reconciled_at=T1,
        )


if __name__ == "__main__":
    unittest.main()
