import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from intel_v2.handoff import (
    add_watch,
    claim_id_for,
    confirm_handoff,
    empty_state,
    find_handoff,
    handoff_markdown,
    handoff_review_warnings,
    sync_with_publication,
    sync_with_detail_rechecks,
    tracking_projection,
)


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-v2-shadow-brief.py"
spec = importlib.util.spec_from_file_location("build_v2_shadow_brief", SCRIPT)
build_v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_v2)

CLI_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/handoff-state.py"
cli_spec = importlib.util.spec_from_file_location("handoff_state_cli", CLI_SCRIPT)
handoff_cli = importlib.util.module_from_spec(cli_spec)
cli_spec.loader.exec_module(handoff_cli)


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

    def _confirmed_then_revised(self):
        """Confirm v1, then let the official source publish a corrected version."""
        first = item()
        state, watch, _ = add_watch(empty_state(), first, created_at=T0)
        state, first_handoff = confirm_handoff(
            state,
            current_items={first["identity"]: first},
            publication={"collection_run_id": "RUN-1", "brief_sha256": "c" * 64},
            generated_at=T0,
            confirmed_at=T0,
        )
        changed = item(version=2)
        revised = sync_with_publication(
            state,
            previous_items={first["identity"]: first},
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
            }],
            observed_at=T1,
            collection_run_id="RUN-2",
        )
        return revised, first_handoff, changed, watch

    def test_review_warnings_bind_only_to_the_changed_source_version(self):
        first = item()
        second = item(identity="S-001:event-2")
        state, _, _ = add_watch(empty_state(), first, created_at=T0)
        state, _, _ = add_watch(state, second, created_at=T0)
        state, first_handoff = confirm_handoff(
            state,
            current_items={first["identity"]: first, second["identity"]: second},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )
        changed = item(version=2)
        revised = sync_with_publication(
            state,
            previous_items={first["identity"]: first, second["identity"]: second},
            current_items={changed["identity"]: changed, second["identity"]: second},
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

        warnings = handoff_review_warnings(revised, first_handoff)

        self.assertEqual([row["claim_id"] for row in warnings], [claim_id_for(first["identity"], 1)])
        self.assertEqual(warnings[0]["warning"], "NEEDS_REVIEW")
        self.assertEqual(warnings[0]["confirmed_source_version"], 1)
        self.assertEqual(warnings[0]["latest_source_version"], 2)
        self.assertEqual(warnings[0]["evidence_locator"], f"{first['identity']}#v1")
        self.assertNotIn(claim_id_for(second["identity"], 1), {row["claim_id"] for row in warnings})

    def test_export_warns_on_unreviewed_source_change_without_rewriting_v1(self):
        revised, first_handoff, changed, watch = self._confirmed_then_revised()
        warnings = handoff_review_warnings(revised, first_handoff)

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["watch_id"], watch["watch_id"])
        markdown = handoff_markdown(first_handoff, warnings)
        self.assertIn("c" * 64, markdown)
        self.assertIn("NEEDS_REVIEW", markdown)
        self.assertIn(claim_id_for(item()["identity"], 1), markdown)

        # The confirmed v1 record itself is never rewritten by a later source update.
        self.assertEqual(revised["handoffs"][0], first_handoff)
        self.assertEqual(revised["handoffs"][0]["items"][0]["source_version"], 1)
        self.assertEqual(len(revised["handoffs"]), 1)

        confirmed_v2, second_handoff = confirm_handoff(
            revised,
            current_items={changed["identity"]: changed},
            publication={"collection_run_id": "RUN-2"},
            generated_at=T1,
            confirmed_at=T2,
        )
        self.assertEqual(handoff_review_warnings(confirmed_v2, first_handoff), [])
        self.assertEqual(handoff_review_warnings(confirmed_v2, second_handoff), [])
        self.assertEqual(confirmed_v2["handoffs"][0]["items"][0]["source_version"], 1)
        self.assertEqual(second_handoff["items"][0]["source_version"], 2)

    def test_export_command_publishes_warning_and_exact_evidence(self):
        revised, first_handoff, _, _ = self._confirmed_then_revised()
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "handoff-state.json"
            state_path.write_text(json.dumps(revised, ensure_ascii=False), encoding="utf-8")
            output_path = Path(directory) / "handoff.md"

            exit_code = handoff_cli.command_export(
                argparse.Namespace(
                    handoff_state=state_path,
                    brief_id=first_handoff["brief_id"],
                    format="markdown",
                    output=output_path,
                )
            )

            self.assertEqual(exit_code, 0)
            markdown = output_path.read_text(encoding="utf-8")
            self.assertIn("NEEDS_REVIEW", markdown)
            self.assertIn(f"{item()['identity']}#v1", markdown)
            self.assertIn("https://example.gov.tw/event-1", markdown)

            json_exit = handoff_cli.command_export(
                argparse.Namespace(
                    handoff_state=state_path,
                    brief_id=first_handoff["brief_id"],
                    format="json",
                    output=Path(directory) / "handoff.json",
                )
            )
            self.assertEqual(json_exit, 0)
            exported = json.loads((Path(directory) / "handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(exported["handoff"], revised["handoffs"][0])
            self.assertEqual(
                [row["claim_id"] for row in exported["review_warnings"]],
                [claim_id_for(item()["identity"], 1)],
            )

            with contextlib.redirect_stdout(io.StringIO()):
                handoff_cli.command_export(
                    argparse.Namespace(
                        handoff_state=state_path,
                        brief_id=first_handoff["brief_id"],
                        format="json",
                        output=None,
                    )
                )

    def _detail_recheck_revision(self):
        first = dict(item(), document_version_id="DOCV-OLD")
        state, watch, _ = add_watch(empty_state(), first, created_at=T0)
        state, first_handoff = confirm_handoff(
            state,
            current_items={first["identity"]: first},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )
        revised = sync_with_detail_rechecks(
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
        return revised, first_handoff, watch

    def test_detail_recheck_warning_reports_the_new_document_version(self):
        revised, first_handoff, watch = self._detail_recheck_revision()

        warnings = handoff_review_warnings(revised, first_handoff)

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["watch_id"], watch["watch_id"])
        self.assertEqual(warnings[0]["warning"], "NEEDS_REVIEW")
        self.assertEqual(warnings[0]["confirmed_source_version"], 1)
        self.assertIsNone(warnings[0]["latest_source_version"])
        self.assertEqual(warnings[0]["latest_source_document_version"], "DOCV-NEW")
        markdown = handoff_markdown(first_handoff, warnings)
        self.assertNotIn("vNone", markdown)
        self.assertIn("DOCV-NEW", markdown)
        self.assertIn("source v1", markdown)

    def test_warning_falls_back_to_the_invalidation_record_for_a_stored_claim(self):
        revised, first_handoff, _ = self._detail_recheck_revision()
        stripped = dict(first_handoff)
        stripped["items"] = [
            {key: value for key, value in first_handoff["items"][0].items() if key != "claim_id"}
        ]

        warnings = handoff_review_warnings(revised, stripped)

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["confirmed_source_version"], 1)
        self.assertEqual(warnings[0]["latest_source_document_version"], "DOCV-NEW")
        self.assertEqual(warnings[0]["evidence_locator"], f"{item()['identity']}#v1")

    def test_export_of_unrevised_handoff_has_no_warning(self):
        first = item()
        state, _, _ = add_watch(empty_state(), first, created_at=T0)
        state, handoff = confirm_handoff(
            state,
            current_items={first["identity"]: first},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )

        self.assertEqual(handoff_review_warnings(state, find_handoff(state)), [])
        self.assertNotIn("NEEDS_REVIEW", handoff_markdown(handoff))

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
