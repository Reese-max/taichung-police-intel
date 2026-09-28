import unittest
import importlib.util
from pathlib import Path

from intel_v2.handoff import (
    add_watch,
    claim_id_for,
    confirm_handoff,
    empty_state,
    handoff_markdown,
    sync_with_publication,
    sync_with_detail_rechecks,
    tracking_projection,
)


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-v2-shadow-brief.py"
spec = importlib.util.spec_from_file_location("build_v2_shadow_brief", SCRIPT)
build_v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_v2)


T0 = "2026-09-01T09:00:00+08:00"
T1 = "2026-09-02T09:00:00+08:00"
T2 = "2026-09-03T09:00:00+08:00"


def item(version=1, identity="S-001:event-1", digest=None):
    source_id, stable_key = identity.split(":", 1)
    return {
        "identity": identity,
        "source_id": source_id,
        "stable_key": stable_key,
        "title": "臺中公共活動公告",
        "official_url": "https://example.gov.tw/event-1",
        "version_no": version,
        "normalized_sha256": digest or ("a" if version == 1 else "b") * 64,
        "date_status": "KNOWN",
    }


class HandoffStateTests(unittest.TestCase):
    def test_add_watch_is_idempotent(self):
        state, first, created = add_watch(empty_state(), item(), created_at=T0)
        same_state, second, created_again = add_watch(state, item(), created_at=T1)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["watch_id"], second["watch_id"])
        self.assertEqual(len(same_state["watch_items"]), 1)

    def test_source_revision_preserves_before_after_and_reopens_review(self):
        state, watch, _ = add_watch(empty_state(), item(), created_at=T0)
        state, first_handoff = confirm_handoff(
            state,
            current_items={item()["identity"]: item()},
            publication={"collection_run_id": "RUN-1", "brief_sha256": "1" * 64},
            generated_at=T0,
            confirmed_at=T0,
        )
        changed = item(version=2)
        updated = sync_with_publication(
            state,
            previous_items={item()["identity"]: item()},
            current_items={changed["identity"]: changed},
            events=[{
                "event_id": "EVT-1",
                "identity": changed["identity"],
                "change_type": "DEADLINE_CHANGED",
                "detected_at": T1,
                "before_version": 1,
                "after_version": 2,
                "changed_fields": ["payload.effective_at"],
                "publishable": True,
                "official_url": changed["official_url"],
            }],
            observed_at=T1,
            collection_run_id="RUN-2",
        )

        tracked = updated["watch_items"][watch["watch_id"]]
        self.assertEqual(tracked["status"], "NEEDS_REVIEW")
        self.assertEqual(tracked["invalidations"][0]["before"]["version"], 1)
        self.assertEqual(tracked["invalidations"][0]["after"]["version"], 2)
        self.assertEqual(tracked["invalidations"][0]["affected_claims"][0]["brief_id"], first_handoff["brief_id"])
        self.assertEqual(
            tracked["invalidations"][0]["affected_claims"][0]["claim_id"],
            claim_id_for(item()["identity"], 1),
        )
        self.assertEqual(updated["handoffs"][0]["brief_id"], first_handoff["brief_id"])

    def test_detail_recheck_reopens_only_matching_confirmed_claim(self):
        current = dict(item(), document_version_id="DOCV-OLD")
        state, watch, _ = add_watch(empty_state(), current, created_at=T0)
        state, first_handoff = confirm_handoff(
            state,
            current_items={current["identity"]: current},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )
        updated = sync_with_detail_rechecks(
            state,
            [{
                "source_id": "S-001",
                "stable_key": "event-1",
                "classification": {
                    "status": "MATERIAL_CHANGE",
                    "review_required": True,
                    "observed_at": T1,
                    "changed_fields": ["effective_at"],
                    "before": {"document_version_id": "DOCV-OLD", "normalized_text_sha256": "a" * 64},
                    "after": {"document_version_id": "DOCV-NEW", "normalized_text_sha256": "b" * 64},
                },
            }],
            observed_at=T1,
        )
        tracked = updated["watch_items"][watch["watch_id"]]
        self.assertEqual(tracked["status"], "NEEDS_REVIEW")
        self.assertEqual(len(tracked["invalidations"]), 1)
        self.assertEqual(tracked["invalidations"][0]["affected_claims"][0]["brief_id"], first_handoff["brief_id"])
        self.assertEqual(tracked["invalidations"][0]["affected_claims"][0]["source_document_version"], "DOCV-OLD")
        rerun = sync_with_detail_rechecks(updated, [
            {
                "source_id": "S-001",
                "stable_key": "event-1",
                "classification": {
                    "status": "MATERIAL_CHANGE",
                    "review_required": True,
                    "before": {"document_version_id": "DOCV-OLD"},
                    "after": {"document_version_id": "DOCV-NEW"},
                },
            }
        ], observed_at=T1)
        self.assertEqual(
            len(rerun["watch_items"][watch["watch_id"]]["invalidations"]),
            len(tracked["invalidations"]),
        )

    def test_failed_or_partial_source_does_not_resolve_watch(self):
        state, watch, _ = add_watch(empty_state(), item(), created_at=T0)
        updated = sync_with_publication(
            state,
            previous_items={item()["identity"]: item()},
            current_items={item()["identity"]: item()},
            events=[],
            observed_at=T1,
            collection_run_id="RUN-PARTIAL",
        )
        rows, total = tracking_projection(
            updated,
            current_items={item()["identity"]: item()},
            events=[],
            source_status={"sources": [{
                "source_id": "S-001",
                "source_health": "FAILED",
                "freshness_status": "STALE",
                "intelligence_gaps": ["fetch failed"],
            }]},
        )

        self.assertEqual(updated["watch_items"][watch["watch_id"]]["status"], "WATCHING")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["source_health"], "FAILED")
        self.assertEqual(rows[0]["source_gaps"], ["fetch failed"])

    def test_confirm_v2_keeps_v1_and_reopens_only_changed_watch(self):
        first_item = item()
        second_item = item(identity="S-001:event-2")
        state, first_watch, _ = add_watch(empty_state(), first_item, created_at=T0)
        state, second_watch, _ = add_watch(state, second_item, created_at=T0)
        state, first_handoff = confirm_handoff(
            state,
            current_items={first_item["identity"]: first_item, second_item["identity"]: second_item},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )
        changed = item(version=2)
        state = sync_with_publication(
            state,
            previous_items={first_item["identity"]: first_item, second_item["identity"]: second_item},
            current_items={changed["identity"]: changed, second_item["identity"]: second_item},
            events=[{
                "event_id": "EVT-1",
                "identity": changed["identity"],
                "change_type": "REVISED",
                "detected_at": T1,
                "after_version": 2,
                "changed_fields": ["payload.title"],
                "publishable": True,
            }],
            observed_at=T1,
            collection_run_id="RUN-2",
        )
        state, second_handoff = confirm_handoff(
            state,
            current_items={changed["identity"]: changed, second_item["identity"]: second_item},
            publication={"collection_run_id": "RUN-2"},
            generated_at=T1,
            confirmed_at=T2,
            watch_ids=[first_watch["watch_id"]],
        )

        self.assertEqual(len(state["handoffs"]), 2)
        self.assertEqual(state["handoffs"][0]["brief_id"], first_handoff["brief_id"])
        self.assertEqual(state["handoffs"][0]["items"][0]["source_version"], 1)
        self.assertEqual(state["handoffs"][0]["items"][0]["claim_id"], claim_id_for(first_item["identity"], 1))
        self.assertEqual(second_handoff["items"][0]["source_version"], 2)
        self.assertEqual(second_handoff["items"][0]["claim_id"], claim_id_for(first_item["identity"], 2))
        self.assertEqual(state["watch_items"][first_watch["watch_id"]]["status"], "WATCHING")
        self.assertEqual(state["watch_items"][second_watch["watch_id"]]["status"], "WATCHING")

    def test_projection_caps_display_but_keeps_total(self):
        state = empty_state()
        items = {}
        for index in range(6):
            current = item(identity=f"S-001:event-{index}")
            items[current["identity"]] = current
            state, _, _ = add_watch(state, current, created_at=T0)

        rows, total = tracking_projection(
            state,
            current_items=items,
            events=[],
            source_status={"sources": []},
            limit=5,
        )

        self.assertEqual(len(rows), 5)
        self.assertEqual(total, 6)

    def test_markdown_export_keeps_exact_evidence_locator(self):
        state, _, _ = add_watch(empty_state(), item(), created_at=T0)
        state, handoff = confirm_handoff(
            state,
            current_items={item()["identity"]: item()},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )

        markdown = handoff_markdown(handoff)
        self.assertIn("HANDOFF-", markdown)
        self.assertIn("CLAIM-", markdown)
        self.assertIn("https://example.gov.tw/event-1", markdown)
        self.assertIn("S-001:event-1#v1", markdown)

    def test_generator_projects_persistent_watch_items_into_brief(self):
        current = item()
        state, _, _ = add_watch(empty_state(), current, created_at=T0)
        brief = {"overview": {}, "status_message": "本期沒有新變更。"}

        build_v2.enrich_for_police_users(
            brief,
            [],
            handoff_state=state,
            current_items={current["identity"]: current},
            source_status={"sources": []},
        )

        self.assertEqual(brief["overview"]["tracking_total"], 1)
        self.assertEqual(brief["tracking_items"][0]["watch_id"], next(iter(state["watch_items"])))
        self.assertIn("跨日追蹤", brief["status_message"])


if __name__ == "__main__":
    unittest.main()
