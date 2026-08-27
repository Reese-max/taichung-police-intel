from __future__ import annotations

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from intel_v2.semantics import (
    build_item_version,
    compare_snapshot,
    ensure_slot_is_due,
    feed_item_to_version,
    shadow_brief,
)


TZ = ZoneInfo("Asia/Taipei")
T0 = "2026-08-27T18:31:00+08:00"
T1 = "2026-08-28T06:31:00+08:00"
T2 = "2026-08-28T18:31:00+08:00"


def make_item(
    stable_key: str,
    payload: dict,
    observed_at: str = T0,
    *,
    source_id: str = "S-009",
    raw_sha256: str | None = None,
    published_at: str | None = None,
    effective_at: str | None = None,
):
    return build_item_version(
        source_id=source_id,
        stable_key=stable_key,
        title=payload.get("title", stable_key),
        official_url=f"https://example.gov.tw/{stable_key}",
        payload=payload,
        observed_at=observed_at,
        raw_sha256=raw_sha256,
        published_at=published_at,
        effective_at=effective_at,
    )


class V2SemanticsTest(unittest.TestCase):
    def test_first_snapshot_establishes_baseline_without_new_events(self):
        items = [
            make_item(f"bill-{index}", {"title": f"歷史提案 {index}"})
            for index in range(108)
        ]
        state, events = compare_snapshot(None, items, T0)

        self.assertEqual(events, [])
        self.assertEqual(len(state["items"]), 108)
        self.assertEqual(state["baseline_established_at"], T0)

    def test_unchanged_second_snapshot_emits_no_events(self):
        item = make_item("bill-1", {"title": "既有提案"})
        state, _ = compare_snapshot(None, [item], T0)
        same = make_item("bill-1", {"title": "既有提案"}, T1)

        next_state, events = compare_snapshot(state, [same], T1)

        self.assertEqual(events, [])
        self.assertEqual(next_state["items"][item.identity]["version_no"], 1)

    def test_raw_wrapper_change_without_semantic_change_emits_no_event(self):
        item = make_item(
            "bill-1",
            {"title": "既有提案", "status": "審議中"},
            raw_sha256="a" * 64,
        )
        state, _ = compare_snapshot(None, [item], T0)
        same_semantics = make_item(
            "bill-1",
            {"title": "既有提案", "status": "審議中"},
            T1,
            raw_sha256="b" * 64,
        )

        next_state, events = compare_snapshot(state, [same_semantics], T1)

        self.assertEqual(events, [])
        self.assertEqual(next_state["items"][item.identity]["raw_sha256"], "b" * 64)
        self.assertEqual(next_state["items"][item.identity]["version_no"], 1)

    def test_new_item_without_official_date_uses_first_seen_wording(self):
        baseline, _ = compare_snapshot(
            None,
            [make_item("bill-1", {"title": "既有提案"})],
            T0,
        )
        added = make_item("bill-2", {"title": "新增警政提案"}, T1)

        _, events = compare_snapshot(
            baseline,
            [make_item("bill-1", {"title": "既有提案"}, T1), added],
            T1,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.change_type, "NEW")
        self.assertEqual(event.temporal_basis, "FIRST_SEEN")
        self.assertEqual(event.date_status, "UNVERIFIED_DATE")
        self.assertIn("本次監測首次偵測", event.wording)
        self.assertIn("未提供可驗證的發布日期", event.wording)
        self.assertNotIn("官方今日發布", event.wording)

    def test_new_item_with_official_date_keeps_official_date_basis(self):
        baseline, _ = compare_snapshot(None, [], T0)
        added = make_item(
            "report-1",
            {"title": "新專案報告"},
            T1,
            published_at="2026-08-28T00:00:00+08:00",
        )

        _, events = compare_snapshot(baseline, [added], T1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].temporal_basis, "OFFICIAL_DATE")
        self.assertEqual(events[0].occurred_at, "2026-08-28T00:00:00+08:00")

    def test_same_stable_key_with_content_change_is_revised_not_new(self):
        original = make_item(
            "bill-1",
            {"title": "霧峰分局整建", "detail": "原始內容"},
        )
        state, _ = compare_snapshot(None, [original], T0)
        revised = make_item(
            "bill-1",
            {"title": "霧峰分局整建", "detail": "修正內容"},
            T1,
        )

        next_state, events = compare_snapshot(state, [revised], T1)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].change_type, "REVISED")
        self.assertEqual(events[0].before_version, 1)
        self.assertEqual(events[0].after_version, 2)
        self.assertEqual(next_state["items"][original.identity]["version_no"], 2)

    def test_status_change_has_specific_event_type(self):
        original = make_item(
            "bill-1",
            {"title": "警政提案", "status": "待審"},
        )
        state, _ = compare_snapshot(None, [original], T0)
        changed = make_item(
            "bill-1",
            {"title": "警政提案", "status": "通過"},
            T1,
        )

        _, events = compare_snapshot(state, [changed], T1)

        self.assertEqual(events[0].change_type, "STATUS_CHANGED")
        self.assertIn("status", events[0].changed_fields)

    def test_deadline_change_has_specific_event_type(self):
        original = make_item(
            "agenda-1",
            {"title": "警消環衛質詢", "meeting_at": "2026-09-01"},
            source_id="S-006",
        )
        state, _ = compare_snapshot(None, [original], T0)
        changed = make_item(
            "agenda-1",
            {"title": "警消環衛質詢", "meeting_at": "2026-09-03"},
            T1,
            source_id="S-006",
        )

        _, events = compare_snapshot(state, [changed], T1)

        self.assertEqual(events[0].change_type, "DEADLINE_CHANGED")

    def test_removal_requires_two_complete_observations(self):
        original = make_item("bill-1", {"title": "待確認提案"})
        state, _ = compare_snapshot(None, [original], T0)

        first_missing_state, first_events = compare_snapshot(state, [], T1)
        self.assertEqual(len(first_events), 1)
        self.assertEqual(first_events[0].change_type, "REMOVAL_CANDIDATE")
        self.assertFalse(first_events[0].publishable)

        second_missing_state, second_events = compare_snapshot(
            first_missing_state,
            [],
            T2,
        )
        self.assertEqual(len(second_events), 1)
        self.assertEqual(second_events[0].change_type, "REMOVED")
        self.assertTrue(second_events[0].publishable)
        self.assertFalse(second_missing_state["items"][original.identity]["is_current"])

    def test_partial_snapshot_does_not_advance_removal(self):
        original = make_item("bill-1", {"title": "來源暫時不完整"})
        state, _ = compare_snapshot(None, [original], T0)

        next_state, events = compare_snapshot(
            state,
            [],
            T1,
            snapshot_complete=False,
        )

        self.assertEqual(events, [])
        self.assertEqual(next_state["items"][original.identity]["missing_streak"], 0)
        self.assertTrue(next_state["items"][original.identity]["is_current"])

    def test_duplicate_stable_identity_is_rejected(self):
        first = make_item("bill-1", {"title": "重複一"})
        second = make_item("bill-1", {"title": "重複二"})

        with self.assertRaisesRegex(ValueError, "duplicate stable identity"):
            compare_snapshot(None, [first, second], T0)

    def test_future_evening_slot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "FUTURE_SLOT"):
            ensure_slot_is_due(
                "EVENING",
                date(2026, 8, 27),
                datetime(2026, 8, 27, 7, 42, tzinfo=TZ),
            )

    def test_due_slot_is_accepted(self):
        scheduled = ensure_slot_is_due(
            "MORNING",
            date(2026, 8, 27),
            datetime(2026, 8, 27, 6, 31, tzinfo=TZ),
        )
        self.assertEqual(scheduled.hour, 6)
        self.assertEqual(scheduled.minute, 30)

    def test_legacy_run_fields_do_not_change_semantic_hash(self):
        base = {
            "stable_id": "FEED-S-009-1",
            "source_id": "S-009",
            "title": "警政提案",
            "official_url": "https://example.gov.tw/proposal/1",
            "committee": "警消環衛委員會-警察",
            "published_at": None,
            "effective_at": None,
            "next_milestone": None,
            "content_sha256": "a" * 64,
            "fetched_at": T0,
            "freshness_status": "FRESH",
            "eligibility": "HOME_CANDIDATE",
            "change_type": "CONFIRMED",
        }
        changed_run_metadata = {
            **base,
            "fetched_at": T1,
            "freshness_status": "STALE",
            "eligibility": "INELIGIBLE_STALE",
            "change_type": "UNCHANGED",
        }

        first = feed_item_to_version(base, T0)
        second = feed_item_to_version(changed_run_metadata, T1)

        self.assertEqual(first.normalized_sha256, second.normalized_sha256)

    def test_shadow_brief_separates_archive_from_current_changes(self):
        feed_items = []
        for index in range(20):
            feed_items.append(
                {
                    "stable_id": f"FEED-S-009-{index}",
                    "source_id": "S-009",
                    "title": f"歷史提案 {index}",
                    "official_url": f"https://example.gov.tw/proposal/{index}",
                    "committee": "警消環衛委員會-警察",
                    "published_at": None,
                    "content_sha256": f"{index:064x}",
                    "change_type": "CONFIRMED",
                    "eligibility": "HOME_CANDIDATE" if index < 19 else "INELIGIBLE_NO_DATE",
                }
            )
        feed = {
            "schema_version": 1,
            "generated_at": T0,
            "collection_run_id": "CR-DEMO-20260827-EVENING-SCHEDULE",
            "items": feed_items,
        }
        versions = [feed_item_to_version(item, T0) for item in feed_items]
        state, events = compare_snapshot(None, versions, T0)

        brief = shadow_brief(feed=feed, state=state, events=events, generated_at=T0)

        self.assertEqual(brief["overview"]["archive_total"], 20)
        self.assertEqual(brief["overview"]["current_change_count"], 0)
        self.assertEqual(brief["overview"]["legacy_home_candidate_count"], 19)
        self.assertIn("未偵測到可確認的新增或修正", brief["status_message"])
        issue_codes = {issue["code"] for issue in brief["quality_issues"]}
        self.assertIn("HIGH_LEGACY_ELIGIBILITY_RATIO", issue_codes)
        self.assertIn("LEGACY_CONFIRMED_AS_CURRENT", issue_codes)


if __name__ == "__main__":
    unittest.main()
