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
    set_watch_status,
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

    def test_terminal_watch_status_survives_publication_and_detail_changes(self):
        for status in ["RESOLVED", "DISMISSED"]:
            with self.subTest(status=status):
                first = dict(item(), document_version_id="DOCV-OLD")
                state, watch, _ = add_watch(empty_state(), first, created_at=T0)
                state, confirmed = confirm_handoff(state, current_items={first["identity"]: first},
                    publication={}, generated_at=T0, confirmed_at=T0)
                terminal = set_watch_status(state, watch["watch_id"], status, updated_at=T1)
                current = dict(item(version=2), document_version_id="DOCV-NEW")
                synced = sync_with_publication(terminal, previous_items={first["identity"]: first},
                    current_items={current["identity"]: current}, events=[], observed_at=T1, collection_run_id="RUN-2")
                self.assertEqual(synced["watch_items"][watch["watch_id"]]["status"], status)
                detailed = sync_with_detail_rechecks(synced, [{"source_id": first["source_id"],
                    "stable_key": first["stable_key"], "classification": {"review_required": True,
                    "status": "MATERIAL_CHANGE", "before": {"document_version_id": "DOCV-NEW"},
                    "after": {"document_version_id": "DOCV-LATER"}}}], observed_at=T2)
                self.assertEqual(detailed["watch_items"][watch["watch_id"]]["status"], status)
                rows, total = tracking_projection(detailed, current_items={current["identity"]: current}, events=[], source_status={})
                self.assertEqual((rows, total), ([], 0))
                self.assertEqual(detailed["handoffs"], [confirmed])
                self.assertEqual(len(detailed["watch_items"][watch["watch_id"]]["invalidations"]), 2)

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

    def test_budget_refused_recheck_never_invalidates_a_confirmed_claim(self):
        current = dict(item(), document_version_id="DOCV-OLD")
        state, watch, _ = add_watch(empty_state(), current, created_at=T0)
        state, _ = confirm_handoff(
            state,
            current_items={current["identity"]: current},
            publication={"collection_run_id": "RUN-1"},
            generated_at=T0,
            confirmed_at=T0,
        )
        updated = sync_with_detail_rechecks(
            state,
            [
                {
                    "source_id": "S-001",
                    "stable_key": "event-1",
                    "status": "SKIPPED",
                    "reason": "BACKOFF_ACTIVE",
                    "classification": None,
                }
            ],
            observed_at=T1,
        )
        self.assertEqual(updated["watch_items"][watch["watch_id"]]["status"], "WATCHING")
        self.assertEqual(updated["watch_items"][watch["watch_id"]]["invalidations"], [])
        with self.assertRaisesRegex(ValueError, "classification is required"):
            sync_with_detail_rechecks(state, [{"source_id": "S-001", "stable_key": "event-1"}], observed_at=T1)

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
        self.assertIn("a" * 64, markdown)
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

    def test_retained_source_cannot_confirm_away_a_known_newer_version_or_hash(self):
        revised, first_handoff, changed, _ = self._confirmed_then_revised()
        original = json.dumps(revised, sort_keys=True)
        for retained in [item(), dict(changed, normalized_sha256="c" * 64)]:
            with self.subTest(version=retained["version_no"]):
                with self.assertRaisesRegex(ValueError, "known source revision"):
                    confirm_handoff(revised, current_items={retained["identity"]: retained},
                        publication={"collection_run_id": "RETAINED"}, generated_at=T2, confirmed_at=T2)
                self.assertEqual(json.dumps(revised, sort_keys=True), original)
                self.assertEqual(revised["handoffs"][0], first_handoff)
                self.assertEqual(len(handoff_review_warnings(revised, first_handoff)), 1)

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

    def test_retained_document_version_cannot_clear_a_detail_recheck(self):
        revised, first_handoff, _ = self._detail_recheck_revision()
        old = dict(item(), document_version_id="DOCV-OLD")
        original = json.dumps(revised, sort_keys=True)
        with self.assertRaisesRegex(ValueError, "known document revision"):
            confirm_handoff(revised, current_items={old["identity"]: old}, publication={},
                generated_at=T2, confirmed_at=T2)
        self.assertEqual(json.dumps(revised, sort_keys=True), original)
        current = dict(old, document_version_id="DOCV-NEW")
        confirmed, latest = confirm_handoff(revised, current_items={current["identity"]: current},
            publication={}, generated_at=T2, confirmed_at=T2)
        self.assertEqual(latest["items"][0]["source_document_version"], "DOCV-NEW")
        self.assertEqual(confirmed["handoffs"][0], first_handoff)

    def test_reviewed_document_revision_still_refuses_retained_old_document(self):
        revised, first_handoff, watch = self._detail_recheck_revision()
        current = dict(item(), document_version_id="DOCV-NEW")
        confirmed, second = confirm_handoff(revised, current_items={current["identity"]: current},
            publication={}, generated_at=T2, confirmed_at=T2)
        original = json.dumps(confirmed, sort_keys=True)
        old = dict(current, document_version_id="DOCV-OLD")
        with self.assertRaisesRegex(ValueError, "known document revision"):
            confirm_handoff(confirmed, current_items={old["identity"]: old}, publication={},
                generated_at=T2, confirmed_at=T2)
        self.assertEqual(json.dumps(confirmed, sort_keys=True), original)
        later = dict(item(version=2), document_version_id="DOCV-LATER")
        synced = sync_with_publication(confirmed, previous_items={current["identity"]: current},
            current_items={later["identity"]: later}, events=[], observed_at=T2, collection_run_id="RUN-3")
        updated, third = confirm_handoff(synced, current_items={later["identity"]: later},
            publication={}, generated_at=T2, confirmed_at=T2)
        self.assertEqual(third["items"][0]["source_document_version"], "DOCV-LATER")
        self.assertEqual(updated["handoffs"][:2], [first_handoff, second])
        self.assertEqual(updated["watch_items"][watch["watch_id"]]["invalidations"][0]["resolved_in_handoff_id"], second["brief_id"])

    def test_retained_publication_cannot_reverse_a_reviewed_same_version_correction(self):
        old = dict(item(), document_version_id="DOCV-OLD")
        state, watch, _ = add_watch(empty_state(), old, created_at=T0)
        state, first = confirm_handoff(state, current_items={old["identity"]: old},
            publication={}, generated_at=T0, confirmed_at=T0)
        for current in [dict(old, normalized_sha256="c" * 64), dict(old, document_version_id="DOCV-NEW")]:
            with self.subTest(hash=current["normalized_sha256"], document=current["document_version_id"]):
                synced = sync_with_publication(state, previous_items={old["identity"]: old},
                    current_items={current["identity"]: current}, events=[], observed_at=T1, collection_run_id="RUN-2")
                self.assertEqual(synced["watch_items"][watch["watch_id"]]["status"], "NEEDS_REVIEW")
                reviewed, second = confirm_handoff(synced, current_items={current["identity"]: current},
                    publication={}, generated_at=T1, confirmed_at=T1)
                for observed in [T0, T1]:
                    retained = sync_with_publication(reviewed, previous_items={current["identity"]: current},
                        current_items={old["identity"]: old}, events=[], observed_at=observed, collection_run_id="RETAINED")
                    original = json.dumps(retained, sort_keys=True)
                    with self.assertRaisesRegex(ValueError, "known (source|document) revision"):
                        confirm_handoff(retained, current_items={old["identity"]: old},
                            publication={}, generated_at=T2, confirmed_at=T2)
                    self.assertEqual(json.dumps(retained, sort_keys=True), original)
                    self.assertEqual(len(retained["watch_items"][watch["watch_id"]]["invalidations"]), 1)
                later = sync_with_publication(reviewed, previous_items={current["identity"]: current},
                    current_items={old["identity"]: old}, events=[], observed_at=T2, collection_run_id="RUN-3")
                self.assertEqual(handoff_review_warnings(later, first), [])
                self.assertEqual(len(handoff_review_warnings(later, second)), 1)
                final, third = confirm_handoff(later, current_items={old["identity"]: old},
                    publication={}, generated_at=T2, confirmed_at=T2)
                self.assertEqual(final["handoffs"][:2], [first, second])
                self.assertEqual(third["items"][0]["source_sha256"], old["normalized_sha256"])

    def test_retained_detail_receipt_cannot_reverse_a_reviewed_document(self):
        revised, first, watch = self._detail_recheck_revision()
        current = dict(item(), document_version_id="DOCV-NEW")
        reviewed, second = confirm_handoff(revised, current_items={current["identity"]: current},
            publication={}, generated_at=T2, confirmed_at=T2)
        old = dict(item(), document_version_id="DOCV-OLD")
        def receipt(observed):
            return {"source_id": "S-001", "stable_key": "event-1", "classification": {
                "status": "MATERIAL_CHANGE", "review_required": True, "observed_at": observed,
                "before": {"document_version_id": "DOCV-PREDECESSOR"},
                "after": {"document_version_id": "DOCV-OLD"}}}
        for observed in [T0, T1]:
            retained = sync_with_detail_rechecks(reviewed, [receipt(observed)], observed_at=T2)
            self.assertEqual(retained["handoffs"], [first, second])
            self.assertEqual(len(retained["watch_items"][watch["watch_id"]]["invalidations"]), 1)
            with self.assertRaisesRegex(ValueError, "known document revision"):
                confirm_handoff(retained, current_items={old["identity"]: old}, publication={},
                    generated_at=T2, confirmed_at=T2)
        later = sync_with_detail_rechecks(reviewed, [receipt(T2)], observed_at=T2)
        final, third = confirm_handoff(later, current_items={old["identity"]: old}, publication={},
            generated_at=T2, confirmed_at=T2)
        self.assertEqual(final["handoffs"][:2], [first, second])
        self.assertEqual(third["items"][0]["source_document_version"], "DOCV-OLD")

    def test_equal_time_detail_predecessor_cannot_clear_pending_review(self):
        pending, first, watch = self._detail_recheck_revision()
        old = dict(item(), document_version_id="DOCV-OLD")
        original = json.dumps(pending, sort_keys=True)
        retained = sync_with_detail_rechecks(pending, [{"source_id": "S-001", "stable_key": "event-1",
            "classification": {"review_required": True, "status": "MATERIAL_CHANGE", "observed_at": T1,
                "before": {"document_version_id": "DOCV-PREDECESSOR"}, "after": {"document_version_id": "DOCV-OLD"}}}], observed_at=T1)
        self.assertEqual(json.dumps(pending, sort_keys=True), original)
        self.assertEqual(len(retained["watch_items"][watch["watch_id"]]["invalidations"]), 1)
        with self.assertRaisesRegex(ValueError, "known document revision"):
            confirm_handoff(retained, current_items={old["identity"]: old}, publication={}, generated_at=T2, confirmed_at=T2)

    def test_unchanged_publication_advances_the_detail_replay_clock(self):
        revised, _, watch = self._detail_recheck_revision()
        current = dict(item(), document_version_id="DOCV-NEW")
        reviewed, _ = confirm_handoff(revised, current_items={current["identity"]: current}, publication={}, generated_at=T1, confirmed_at=T1)
        later = "2026-09-04T09:00:00+08:00"
        observed = sync_with_publication(reviewed, previous_items={current["identity"]: current}, current_items={current["identity"]: current}, events=[], observed_at=later, collection_run_id="UNCHANGED")
        retained = sync_with_detail_rechecks(observed, [{"source_id": "S-001", "stable_key": "event-1",
            "classification": {"review_required": True, "status": "MATERIAL_CHANGE", "observed_at": T2,
                "before": {"document_version_id": "DOCV-PREDECESSOR"}, "after": {"document_version_id": "DOCV-OLD"}}}], observed_at=later)
        self.assertEqual(len(retained["watch_items"][watch["watch_id"]]["invalidations"]), 1)
        old = dict(item(), document_version_id="DOCV-OLD")
        with self.assertRaisesRegex(ValueError, "known document revision"):
            confirm_handoff(retained, current_items={old["identity"]: old}, publication={}, generated_at=later, confirmed_at=later)

    def test_later_publication_and_detail_recurrences_coalesce_only_pending_observations(self):
        old = dict(item(), document_version_id="DOCV-OLD")
        new = dict(item(digest="c" * 64), document_version_id="DOCV-NEW")
        for path in ["publication", "detail"]:
            with self.subTest(path=path):
                new = dict(new, normalized_sha256="c" * 64 if path == "publication" else old["normalized_sha256"])
                state, watch, _ = add_watch(empty_state(), old, created_at=T0)
                state, _ = confirm_handoff(state, current_items={old["identity"]: old}, publication={}, generated_at=T0, confirmed_at=T0)
                def observe(current, previous, at):
                    if path == "publication":
                        return sync_with_publication(state, previous_items={previous["identity"]: previous}, current_items={current["identity"]: current}, events=[], observed_at=at, collection_run_id="CYCLE")
                    return sync_with_detail_rechecks(state, [{"source_id": "S-001", "stable_key": "event-1", "classification": {
                        "review_required": True, "status": "MATERIAL_CHANGE", "observed_at": at,
                        "before": {"document_version_id": previous["document_version_id"]}, "after": {"document_version_id": current["document_version_id"]}}}], observed_at=at)
                for current, previous, at in [(new, old, T1), (old, new, T2)]:
                    state = observe(current, previous, at)
                    state, _ = confirm_handoff(state, current_items={current["identity"]: current}, publication={}, generated_at=at, confirmed_at=at)
                original_handoffs = json.loads(json.dumps(state["handoffs"]))
                first_receipt = state["watch_items"][watch["watch_id"]]["invalidations"][0]["resolved_in_handoff_id"]
                later = "2026-09-04T09:00:00+08:00"
                repeated = "2026-09-05T09:00:00+08:00"
                state = observe(new, old, later)
                state = observe(new, old, repeated)
                self.assertEqual(len(state["watch_items"][watch["watch_id"]]["invalidations"]), 3)
                final, handoff = confirm_handoff(state, current_items={new["identity"]: new}, publication={}, generated_at=repeated, confirmed_at=repeated)
                self.assertEqual(final["handoffs"][:3], original_handoffs)
                self.assertEqual(handoff["items"][0]["source_document_version"], "DOCV-NEW")
                self.assertEqual(final["watch_items"][watch["watch_id"]]["invalidations"][0]["resolved_in_handoff_id"], first_receipt)

    def test_unchanged_bound_detail_advances_the_retained_receipt_clock(self):
        revised, _, watch = self._detail_recheck_revision()
        current = dict(item(), document_version_id="DOCV-NEW")
        reviewed, _ = confirm_handoff(revised, current_items={current["identity"]: current}, publication={}, generated_at=T1, confirmed_at=T1)
        later = "2026-09-04T09:00:00+08:00"
        observed = sync_with_detail_rechecks(reviewed, [{"source_id": "S-001", "stable_key": "event-1",
            "classification": {"review_required": False, "status": "UNCHANGED", "observed_at": later,
                "before": {"document_version_id": "DOCV-NEW"}, "after": {"document_version_id": "DOCV-NEW"}}}], observed_at=later)
        self.assertEqual(observed["watch_items"][watch["watch_id"]]["last_source_observed_at"], later)
        retained = sync_with_detail_rechecks(observed, [{"source_id": "S-001", "stable_key": "event-1",
            "classification": {"review_required": True, "status": "MATERIAL_CHANGE", "observed_at": T2,
                "before": {"document_version_id": "DOCV-PREDECESSOR"}, "after": {"document_version_id": "DOCV-OLD"}}}], observed_at=later)
        self.assertEqual(len(retained["watch_items"][watch["watch_id"]]["invalidations"]), 1)

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
