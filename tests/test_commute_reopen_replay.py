"""Regression tests for the v6 commute-road reopen replay (issue #108).

The gold cases under ``eval/gold/v6/commute-reopen-v1`` are the human standard
answer for "which updates may the station prompt at each reopen". These tests
check that the current implementation reproduces them, and that every matching
and dedupe decision is read live rather than from a frozen answer key.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "eval" / "gold" / "v6" / "commute-reopen-v1"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ev = _load("govintel_eval", "scripts/evaluate-govintel.py")
rc = _load("commute_reopen_cli", "scripts/replay-commute-reopen.py")

from intel_v2 import commute_replay as replay  # noqa: E402


def gold_cases() -> tuple[dict, list[dict]]:
    return rc.load_gold_cases(DATA_DIR / "manifest.json")


def run_arm(arm: str, scenario=None, manifest=None, cases=None):
    scenario = scenario if scenario is not None else replay.load_scenario(DATA_DIR / "session.json")
    manifest, cases = gold_cases() if manifest is None else (manifest, cases)
    overrides = rc.load_arms(manifest)[arm]
    return rc.run_arm(arm, overrides, scenario, manifest, cases)


def scenario_copy() -> dict:
    return json.loads((DATA_DIR / "session.json").read_text(encoding="utf-8"))


def run_probe(name: str, scenario: dict | None = None) -> dict:
    manifest, cases = gold_cases()
    return rc.run_arm(
        name,
        rc.PROBES[name]["overrides"],
        scenario if scenario is not None else replay.load_scenario(DATA_DIR / "session.json"),
        manifest,
        cases,
    )["report"]["reopen_unread"]


class ScenarioContractTests(unittest.TestCase):
    def setUp(self):
        self.scenario = replay.load_scenario(DATA_DIR / "session.json")

    def test_scenario_is_versioned_synthetic_and_labelled(self):
        self.assertEqual(self.scenario["schema_version"], replay.SCENARIO_SCHEMA_VERSION)
        self.assertEqual(self.scenario["fingerprint"], replay.FINGERPRINT)
        self.assertIs(self.scenario["synthetic"], True)
        self.assertIn("合成", self.scenario["notice"])
        self.assertIn("不代表任何真實路況", self.scenario["notice"])
        for update in self.scenario["updates"]:
            self.assertEqual(update["rights"], "SYNTHETIC_NO_REAL_ROAD")

    def test_every_update_content_hash_matches_its_payload(self):
        for update in self.scenario["updates"]:
            self.assertEqual(
                update["content_sha256"],
                replay.normalized_payload_sha256(update["payload"], replay.VOLATILE_FIELDS),
                update["update_id"],
            )

    def test_tampered_payload_is_rejected_instead_of_scored(self):
        broken = scenario_copy()
        broken["updates"][0]["payload"]["text"] = "被竄改的原文"
        with self.assertRaisesRegex(replay.ScenarioError, "content_sha256"):
            replay.validate_scenario(broken)

    def test_non_synthetic_scenario_is_rejected(self):
        broken = scenario_copy()
        broken["synthetic"] = False
        with self.assertRaisesRegex(replay.ScenarioError, "synthetic"):
            replay.validate_scenario(broken)

    def test_unverifiable_interval_is_rejected_not_treated_as_irrelevant(self):
        broken = scenario_copy()
        broken["updates"][0]["payload"]["effective_to"] = "2026-10-04T17:00:00+08:00"
        broken["updates"][0]["content_sha256"] = replay.normalized_payload_sha256(
            broken["updates"][0]["payload"], replay.VOLATILE_FIELDS
        )
        with self.assertRaisesRegex(replay.ScenarioError, "effective_to precedes effective_from"):
            replay.validate_scenario(broken)

    def test_daily_band_that_can_never_intersect_is_rejected(self):
        broken = scenario_copy()
        broken["conditions"][0]["daily"] = {"start": "22:00", "end": "06:00"}
        with self.assertRaisesRegex(replay.ScenarioError, "daily band must end after it starts"):
            replay.validate_scenario(broken)
        broken = scenario_copy()
        broken["conditions"][0]["daily"] = {"start": "09:00", "end": "09:00"}
        with self.assertRaisesRegex(replay.ScenarioError, "daily band must end after it starts"):
            replay.validate_scenario(broken)

    def test_malformed_clock_is_rejected_with_a_scenario_error(self):
        broken = scenario_copy()
        broken["conditions"][0]["daily"]["end"] = "17:00:00"
        with self.assertRaisesRegex(replay.ScenarioError, "must be HH:MM"):
            replay.validate_scenario(broken)

    def test_documents_acquired_after_the_cutoff_are_never_replayed(self):
        broken = scenario_copy()
        broken["updates"].append(
            {
                "update_id": "UPD-999",
                "document_id": "DOC-FUTURE",
                "source_id": "S-001",
                "origin": "OFFICIAL",
                "rights": "SYNTHETIC_NO_REAL_ROAD",
                "acquired_at": "2026-10-20T09:00:00+08:00",
                "published_at": "2026-10-20T09:00:00+08:00",
                "upstream_update_id": None,
                "content_sha256": "0" * 64,
                "payload": broken["updates"][0]["payload"],
            }
        )
        broken["updates"][-1]["content_sha256"] = replay.normalized_payload_sha256(
            broken["updates"][-1]["payload"], replay.VOLATILE_FIELDS
        )
        receipt = replay.replay(broken)
        self.assertNotIn("UPD-999", receipt["publication_receipt"]["acquired_update_ids"])
        decisions = {
            item["update_id"]: item["decision"]
            for point in receipt["reopen_points"]
            for item in point["dispositions"]
        }
        self.assertEqual(decisions["UPD-999"], replay.NOT_YET_ACQUIRED)


class GoldCaseTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.cases = gold_cases()

    def test_all_cases_are_synthetic_reopen_cases(self):
        self.assertEqual(len(self.cases), 9)
        self.assertTrue(all(case["task"] == "reopen_unread" for case in self.cases))
        self.assertTrue(all(case.get("synthetic") is True for case in self.cases))
        self.assertTrue(all(case.get("rationale") for case in self.cases))

    def test_every_case_records_a_gap_list_and_a_condition_state(self):
        for case in self.cases:
            self.assertIsInstance(case["expected"]["gap_kinds"], list)
            self.assertIsInstance(case["expected"]["observation_status"], str)
            self.assertIn(
                case["expected"]["observation_status"],
                self.manifest["observation_statuses"],
                case["case_id"],
            )
            self.assertEqual(
                set(case["expected"]["condition_status"]),
                {"C-ROAD-AB", "C-DISTRICT-XITUN"},
                case["case_id"],
            )

    def test_station_states_are_four_distinct_observations(self):
        statuses = {case["expected"]["observation_status"] for case in self.cases}
        self.assertEqual(
            statuses,
            {
                replay.NEW_UPDATES,
                replay.NO_NEW_ITEMS,
                replay.SOURCE_GAP,
                replay.STALE_TRACKED_END,
            },
        )

    def test_event_versions_and_reposts_never_cross_the_dev_holdout_split(self):
        development = set(self.manifest["development_reopen_ids"])
        heldout = set(self.manifest["heldout_reopen_ids"])
        self.assertFalse(development & heldout)
        self.assertEqual(development | heldout, {case["case_id"] for case in self.cases})
        development_updates: set[str] = set()
        heldout_updates: set[str] = set()
        for case in self.cases:
            target = development_updates if case["case_id"] in development else heldout_updates
            target.update(case["expected"]["update_ids"])
        self.assertFalse(development_updates & heldout_updates)

    def test_targets_are_declared_separately_from_results(self):
        self.assertEqual(self.manifest["targets"]["precision"], 0.85)
        self.assertEqual(self.manifest["targets"]["recall"], 0.9)
        self.assertEqual(self.manifest["targets"]["median_total_time_reduction_vs_arm_a"], 0.3)
        self.assertIsNone(self.manifest["arms"]["C_v6"]["results"])


class ReplayBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.receipt = replay.replay(replay.load_scenario(DATA_DIR / "session.json"))
        self.points = {point["reopen_id"]: point for point in self.receipt["reopen_points"]}

    def decisions(self, reopen_id: str) -> dict[str, str]:
        return {item["update_id"]: item["decision"] for item in self.points[reopen_id]["dispositions"]}

    def test_receipt_records_versions_data_hash_clock_and_publication_receipt(self):
        receipt = self.receipt
        self.assertEqual(receipt["fingerprint"], replay.FINGERPRINT)
        self.assertEqual(len(receipt["data_hash"]), 64)
        self.assertEqual(receipt["data_cutoff"], "2026-10-10T09:00:00+08:00")
        self.assertEqual(receipt["clock"]["reopen_count"], 9)
        self.assertEqual(receipt["clock"]["last_reopened_at"], "2026-10-10T08:30:00+08:00")
        self.assertEqual(receipt["versions"]["policy_version"], replay.POLICY_VERSION)
        self.assertEqual(receipt["versions"]["parser_version"], replay.PARSER_VERSION)
        # The offline replay must not imply a model call.
        self.assertIsNone(receipt["versions"]["model_version"])
        self.assertIsNone(receipt["versions"]["prompt_hash"])
        publication = receipt["publication_receipt"]
        self.assertEqual(publication["acquired_count"], len(publication["acquired_update_ids"]))
        self.assertEqual(publication["collection_slots"][0], "SLOT-1")
        self.assertTrue(all(len(row["content_identity_sha256"]) == 64 for row in publication["content_identity"]))

    def test_first_reopen_prompts_the_saved_road_condition(self):
        self.assertEqual(self.points["R1"]["prompted_update_ids"], ["UPD-001"])
        self.assertEqual(self.points["R1"]["observation_status"], replay.NEW_UPDATES)

    def test_repost_duplicate_fetch_and_layout_only_never_alert(self):
        decisions = self.decisions("R2")
        self.assertEqual(decisions["UPD-002"], replay.REPOST_MIRROR)
        self.assertEqual(decisions["UPD-003"], replay.DUPLICATE_FETCH)
        self.assertEqual(decisions["UPD-004"], replay.FORMAT_ONLY)
        self.assertEqual(self.points["R2"]["prompted_update_ids"], [])
        self.assertEqual(self.points["R2"]["observation_status"], replay.NO_NEW_ITEMS)

    def test_read_update_is_not_unread_again_on_the_next_reopen(self):
        decisions = self.decisions("R3")
        self.assertEqual(decisions["UPD-001"], replay.ALREADY_READ)

    def test_deferral_alerts_once_and_moves_the_tracked_end_without_lifting(self):
        self.assertEqual(self.points["R3"]["prompted_update_ids"], ["UPD-005"])
        self.assertEqual(
            self.receipt["condition_summary"]["tracked_end_by_condition"]["C-ROAD-AB"],
            "2026-10-09T17:00:00+08:00",
        )
        self.assertEqual(self.points["R3"]["condition_status"]["C-ROAD-AB"], replay.ACTIVE)
        self.assertIn(replay.GAP_SCHEDULED_END_PASSED, {flag["kind"] for flag in self.points["R3"]["gap_flags"]})

    def test_out_of_window_and_unverifiable_dates_are_kept_unknown(self):
        decisions = self.decisions("R3")
        self.assertEqual(decisions["UPD-010"], replay.OUTSIDE_TRACKED_WINDOW)
        self.assertEqual(decisions["UPD-011"], replay.DATE_UNVERIFIED)

    def test_source_failures_and_missing_documents_never_lift_the_condition(self):
        for reopen_id, kinds in (
            ("R4", {replay.GAP_CONNECTION_FAILURE}),
            (
                "R5",
                {replay.GAP_PARTIAL_COVERAGE, replay.GAP_SOURCE_ITEM_MISSING},
            ),
            ("R6", {replay.GAP_SCHEDULED_END_PASSED}),
        ):
            point = self.points[reopen_id]
            self.assertEqual(point["prompted_update_ids"], [], reopen_id)
            self.assertEqual(point["condition_status"]["C-ROAD-AB"], replay.ACTIVE, reopen_id)
            self.assertTrue(all(flag["auto_lifted"] is False for flag in point["gap_flags"]), reopen_id)
            self.assertTrue(kinds <= {flag["kind"] for flag in point["gap_flags"]}, reopen_id)
        self.assertEqual(self.points["R4"]["observation_status"], replay.SOURCE_GAP)
        self.assertEqual(self.points["R5"]["observation_status"], replay.SOURCE_GAP)
        self.assertEqual(self.points["R6"]["observation_status"], replay.STALE_TRACKED_END)

    def test_a_document_that_disappears_is_reported_as_a_missing_item(self):
        missing = [
            flag
            for flag in self.points["R5"]["gap_flags"]
            if flag["kind"] == replay.GAP_SOURCE_ITEM_MISSING
        ]
        self.assertTrue(missing, "R5 must exercise the missing-item branch")
        self.assertEqual({flag["document_id"] for flag in missing}, {"DOC-ROAD-AB"})

    def test_acquisition_gaps_are_counted_apart_from_the_stale_tracked_end(self):
        counters = self.receipt["counters"]
        acquisition = sum(
            1
            for point in self.receipt["reopen_points"]
            for flag in point["gap_flags"]
            if flag["kind"] in replay.ACQUISITION_GAP_KINDS
        )
        stale = sum(
            1
            for point in self.receipt["reopen_points"]
            for flag in point["gap_flags"]
            if flag["kind"] == replay.GAP_SCHEDULED_END_PASSED
        )
        self.assertEqual(counters["source_acquisition_gap"], acquisition)
        self.assertEqual(counters["stale_tracked_end_observation"], stale)
        self.assertEqual(acquisition, 3)
        self.assertEqual(stale, 4)

    def test_a_reopen_may_not_mark_a_later_update_read(self):
        broken = scenario_copy()
        for reopen in broken["reopen_points"]:
            if reopen["reopen_id"] == "R6":
                reopen["read_update_ids"] = ["UPD-008"]
        with self.assertRaisesRegex(replay.ScenarioError, "not acquired at this reopen"):
            replay.replay(broken)

    def test_a_read_mark_on_an_already_acquired_update_is_honoured(self):
        marked = scenario_copy()
        for reopen in marked["reopen_points"]:
            if reopen["reopen_id"] == "R1":
                reopen["read_update_ids"] = []
        receipt = replay.replay(marked)
        points = {point["reopen_id"]: point for point in receipt["reopen_points"]}
        self.assertEqual(points["R1"]["prompted_update_ids"], ["UPD-001"])
        # Not read at R1, so R2 would prompt it again; this proves the mark is read.
        self.assertEqual(points["R2"]["prompted_update_ids"], ["UPD-001"])

    def test_public_original_availability_is_derived_not_asserted(self):
        for point in self.receipt["reopen_points"]:
            self.assertEqual(
                point["public_original_available"],
                all(point["public_original_by_update_id"][uid] for uid in point["prompted_update_ids"]),
                point["reopen_id"],
            )
        # The official listing keeps every document the station prompted, including
        # after the road condition was cancelled.
        self.assertTrue(self.points["R9"]["public_original_available"])
        vanished = scenario_copy()
        for collection in vanished["collections"]:
            if collection["slot_id"] == "SLOT-11":
                collection["visible_update_ids"] = [
                    uid
                    for uid in collection["visible_update_ids"]
                    if uid not in {"UPD-007", "UPD-008"}
                ]
        dropped = replay.replay(vanished)["reopen_points"][-1]
        self.assertEqual(dropped["prompted_update_ids"], ["UPD-008"])
        self.assertFalse(dropped["public_original_available"])
        self.assertFalse(dropped["public_original_by_update_id"]["UPD-008"])

    def test_original_availability_is_reported_per_update(self):
        availability = self.points["R9"]["public_original_by_update_id"]
        self.assertTrue(availability["UPD-008"])
        self.assertTrue(availability["UPD-006"])
        self.assertFalse(availability["UPD-002"])  # the mirror was never in the official listing

    def test_explicit_lift_is_road_scoped_and_a_new_project_still_matches(self):
        self.assertEqual(self.points["R7"]["prompted_update_ids"], ["UPD-006"])
        self.assertEqual(self.points["R7"]["condition_status"]["C-ROAD-AB"], replay.LIFTED_BY_OFFICIAL_TEXT)
        self.assertEqual(self.points["R7"]["condition_status"]["C-DISTRICT-XITUN"], replay.ACTIVE)
        self.assertEqual(self.receipt["condition_summary"]["lifted_by_update_id"], {"C-ROAD-AB": "UPD-006"})
        self.assertEqual(self.points["R8"]["prompted_update_ids"], ["UPD-007"])
        self.assertTrue(all(point["public_original_available"] for point in self.receipt["reopen_points"]))

    def test_cancel_stops_the_prompt_but_keeps_other_conditions_and_the_original_text(self):
        point = self.points["R9"]
        self.assertEqual(point["prompted_update_ids"], ["UPD-008"])
        self.assertEqual(point["condition_status"]["C-ROAD-AB"], replay.CANCELLED)
        self.assertEqual(point["condition_status"]["C-DISTRICT-XITUN"], replay.ACTIVE)
        self.assertEqual(self.decisions("R9")["UPD-012"], replay.POST_CANCEL)
        self.assertTrue(point["public_original_available"])

    def test_multi_condition_hits_are_reported_without_duplicating_prompts(self):
        multi = self.receipt["multi_condition_update_ids"]
        self.assertEqual(multi, ["UPD-001", "UPD-005", "UPD-006"])
        self.assertEqual(self.points["R1"]["multi_condition_update_ids"], ["UPD-001"])
        self.assertEqual(self.points["R8"]["multi_condition_update_ids"], [])


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.run = run_arm("C_v6")

    def test_full_v6_arm_reproduces_every_gold_expected_set(self):
        report = self.run["report"]["reopen_unread"]
        self.assertEqual((report["tp"], report["fp"], report["fn"]), (5, 0, 0))
        self.assertEqual(report["precision"], 1.0)
        self.assertEqual(report["recall"], 1.0)
        self.assertEqual(report["observation_status_accuracy"], 1.0)
        self.assertEqual(report["condition_status_accuracy"], 1.0)
        breakdown = report["false_alert_breakdown"]
        self.assertEqual(set(breakdown.values()), {0}, breakdown)

    def test_multi_condition_hits_do_not_inflate_true_positives(self):
        report = self.run["report"]["reopen_unread"]
        receipt = self.run["receipt"]
        # Eight (update, condition) pairs behind five prompted updates: three of
        # them matched two tracked conditions. The extra match must never be
        # scored twice.
        self.assertEqual(receipt["counters"]["prompted_condition_pairs"], 8)
        self.assertEqual(receipt["counters"]["prompted_updates"], 5)
        self.assertEqual(report["multi_condition_prompt_count"], 3)
        self.assertEqual(report["tp"], 5)
        self.assertEqual(report["tp"], report["expected_id_denominator"])
        self.assertEqual(report["fp"], 0)

    def test_source_acquisition_gaps_are_listed_separately_from_precision(self):
        report = self.run["report"]["reopen_unread"]
        self.assertEqual(report["source_acquisition_gap"]["reopen_point_count"], 2)
        self.assertEqual(report["false_alert_breakdown"]["missed_while_source_gap"], 0)

    def test_not_run_scopes_are_reported_not_as_pass(self):
        pending = self.run["report"]["not_run"]
        scopes = {(entry["arm"], entry["scope"]) for entry in pending}
        # A_v1 was never run at all; every replayed arm still has no human timing.
        self.assertIn(("A_v1", "arm"), scopes)
        for arm in ("A_v1", "B_v6", "C_v6", "C_RULES_ONLY"):
            self.assertIn((arm, "human_timing"), scopes)
        self.assertNotIn(("C_v6", "arm"), scopes)
        self.assertEqual({entry["status"] for entry in pending}, {"NOT_RUN"})
        self.assertEqual(
            {entry["method_id"] for entry in pending if entry["arm"] == "A_v1"},
            {"A_v1_manual_same_sources_same_cutoff"},
        )

    def test_manifest_arm_definitions_are_what_the_cli_actually_runs(self):
        manifest, _ = gold_cases()
        arms = rc.load_arms(manifest)
        self.assertEqual(
            arms["C_RULES_ONLY"],
            {"semantic_matching": False},
        )
        self.assertEqual(arms["B_v6"]["track_read_state"], False)
        self.assertEqual(arms["C_v6"], {})
        self.assertEqual(arms["A_v1"], {})
        for arm, overrides in arms.items():
            policy = replay.policy_from_mapping(overrides)
            self.assertEqual(policy.to_dict(), run_arm(arm)["policy"])

    def test_a_perfect_reopen_run_also_matches_the_gold_case_fields(self):
        report = self.run["report"]
        self.assertEqual(report["exact_case_match_count"], report["evaluated_cases"])
        self.assertEqual(report["exact_case_match_rate"], 1.0)

    def test_gold_declared_multi_condition_sets_are_scored(self):
        report = self.run["report"]["reopen_unread"]
        self.assertEqual(report["multi_condition_accuracy"], 1.0)
        _, cases = gold_cases()
        declared = {
            case["case_id"]: case["expected"]["multi_condition_update_ids"] for case in cases
        }
        self.assertEqual(declared["R1"], ["UPD-001"])
        self.assertEqual(declared["R8"], [])
        rows = {row["case_id"]: row for row in self.run["report"]["reopen_unread_rows"]}
        self.assertEqual(rows["R1"]["multi_condition_correct"], 1)
        self.assertEqual(rows["R8"]["multi_condition_correct"], 1)
        # The gold set, not the prediction, decides the comparison: a prediction that
        # declares a multi-condition hit where the gold declares none is a miss.
        case = next(case for case in cases if case["case_id"] == "R8")
        prediction = dict(replay.prediction_rows(self.run["receipt"])[7]["prediction"])
        prediction["multi_condition_update_ids"] = ["UPD-007"]
        self.assertEqual(ev.reopen_diagnostics(case, prediction)["multi_condition_correct"], 0)

    def test_rules_only_ablation_is_scored_separately_and_is_worse(self):
        report = run_arm("C_RULES_ONLY")["report"]["reopen_unread"]
        self.assertEqual(report["tp"], 5)
        self.assertEqual(report["fp"], 2)
        self.assertLess(report["precision"], 1.0)
        self.assertEqual(report["fn"], 0)

    def test_rules_only_false_alerts_are_time_reasoning_defects(self):
        breakdown = run_arm("C_RULES_ONLY")["report"]["reopen_unread"]["false_alert_breakdown"]
        self.assertGreater(breakdown["other_false_alert"], 0)
        for counter in (
            "already_read_repeat",
            "layout_only_false_alert",
            "repost_false_alert",
            "duplicate_fetch_false_alert",
            "post_cancel_prompt",
        ):
            self.assertEqual(breakdown[counter], 0, counter)

    def test_search_only_arm_repeats_already_read_updates(self):
        run = run_arm("B_v6")
        report = run["report"]["reopen_unread"]
        self.assertEqual(report["false_alert_breakdown"]["already_read_repeat"], 17)
        self.assertEqual(report["false_alert_breakdown"]["other_false_alert"], 0)
        # Every repeat adds both a condition pair and a false alert, so the raw
        # pair count runs far ahead of the deduplicated true positive count.
        self.assertGreater(run["receipt"]["counters"]["prompted_condition_pairs"], 2 * report["fp"])
        self.assertEqual(report["tp"], report["expected_id_denominator"])

    def test_gap_kinds_are_scored_against_the_gold_gap_list(self):
        report = self.run["report"]["reopen_unread"]
        self.assertEqual(report["gap_kind_accuracy"], 1.0)
        rows = {row["case_id"]: row for row in self.run["report"]["reopen_unread_rows"]}
        self.assertEqual(rows["R4"]["source_gap_kinds"], ["CONNECTION_FAILURE"])
        self.assertEqual(rows["R5"]["source_gap_kinds"], ["PARTIAL_COVERAGE", "SOURCE_ITEM_MISSING"])
        self.assertEqual(rows["R6"]["source_gap_kinds"], ["SCHEDULED_END_PASSED"])

    def test_zero_denominator_is_null_rather_than_a_perfect_score(self):
        manifest, _ = gold_cases()
        report = ev.evaluate(manifest, [], {})
        metrics = report["reopen_unread"]
        self.assertEqual(metrics["tp"], 0)
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["recall"])
        self.assertIsNone(metrics["f1"])
        self.assertIsNone(metrics["observation_status_accuracy"])


class LiveDecisionTests(unittest.TestCase):
    """Every matching and dedupe rule must be able to change the result."""

    def setUp(self):
        self.baseline = run_arm("C_v6")["report"]["reopen_unread"]

    def assertDiffers(self, metrics: dict) -> dict:
        self.assertNotEqual(metrics, self.baseline)
        return metrics

    def test_mirror_is_not_independent_evidence(self):
        self.assertEqual(self.assertDiffers(run_probe("PROBE_MIRROR_AS_EVIDENCE"))["fp"], 1)

    def test_layout_only_revision_is_not_a_substantive_update(self):
        self.assertEqual(self.assertDiffers(run_probe("PROBE_FORMATTING_AS_SUBSTANTIVE"))["fp"], 1)

    def test_repeat_fetch_dedupe_is_live(self):
        self.assertEqual(self.assertDiffers(run_probe("PROBE_DEDUPE_OFF"))["fp"], 1)

    def test_one_alert_per_document_wrongly_dedupes_a_later_correction(self):
        metrics = self.assertDiffers(run_probe("PROBE_ONE_ALERT_PER_DOCUMENT"))
        self.assertEqual(metrics["fn"], 3)
        self.assertEqual(metrics["false_alert_breakdown"]["distinct_correction_wrongly_deduped"], 3)
        self.assertLess(metrics["recall"], 1.0)

    def test_prompting_after_a_cancel_is_attributed_to_the_cancel(self):
        metrics = self.assertDiffers(run_probe("PROBE_PROMPT_AFTER_CANCEL"))
        self.assertEqual(metrics["false_alert_breakdown"]["post_cancel_prompt"], 1)
        self.assertEqual(metrics["false_alert_breakdown"]["other_false_alert"], 0)

    def test_auto_lift_would_hide_the_stale_tracked_end(self):
        metrics = self.assertDiffers(run_probe("PROBE_AUTO_LIFT_ON_TRACKED_END"))
        self.assertLess(metrics["observation_status_accuracy"], 1.0)

    def test_moving_the_cancel_time_changes_the_last_reopen(self):
        later = scenario_copy()
        for condition in later["conditions"]:
            if condition["condition_id"] == "C-ROAD-AB":
                condition["cancelled_at"] = "2026-10-11T00:00:00+08:00"
        metrics = rc.run_arm("CANCEL_MOVED_LATER", {}, later, *gold_cases())["report"]["reopen_unread"]
        self.assertEqual(metrics["fp"], 1)
        last = replay.replay(later)["reopen_points"][-1]
        self.assertIn("UPD-012", last["prompted_update_ids"])

    def test_shared_announcement_never_scales_model_requests(self):
        scenario = replay.load_scenario(DATA_DIR / "session.json")
        base = replay.replay(scenario)
        wider = scenario_copy()
        for index in range(3, 6):
            clone = json.loads(json.dumps(scenario["conditions"][0]))
            clone["condition_id"] = f"C-CLONE-{index:02d}"
            clone["cancelled_at"] = None
            wider["conditions"].append(clone)
        receipt = replay.replay(wider)
        self.assertGreater(receipt["cost_scope"]["tracked_conditions"], base["cost_scope"]["tracked_conditions"])
        self.assertEqual(receipt["cost_scope"]["model_requests"], 0)
        self.assertEqual(base["cost_scope"]["model_requests"], 0)
        self.assertIsNone(receipt["cost_scope"]["human_hours"])
        self.assertEqual(receipt["cost_scope"]["status"], "REPLAY_ONLY")


class FalseAlertBreakdownTests(unittest.TestCase):
    """The per-mode counters must not hide one failure inside another."""

    def build(
        self,
        expected_ids,
        predicted_ids,
        decisions,
        gap_kinds=(),
        gold_gap_kinds=(),
        substances=None,
        cancelled=None,
        status="NEW_UPDATES",
    ):
        case = {
            "case_id": "RC-1",
            "task": "reopen_unread",
            "synthetic": True,
            "input": {"reopen_id": "RC-1"},
            "expected": {
                "update_ids": list(expected_ids),
                "observation_status": "NEW_UPDATES",
                "gap_kinds": sorted(gold_gap_kinds),
                "condition_status": {},
                "multi_condition_update_ids": [],
            },
        }
        prediction = {
            "update_ids": list(predicted_ids),
            "observation_status": status,
            "gap_kinds": sorted(gap_kinds),
            "condition_status": {},
            "decision_by_update_id": decisions,
            "substance_by_update_id": substances or {},
            "cancelled_condition_by_update_id": cancelled or {},
            "multi_condition_update_ids": [],
        }
        return case, prediction

    def test_each_false_alert_mode_gets_its_own_counter(self):
        case, prediction = self.build(
            expected_ids=["U1", "U2"],
            predicted_ids=["U1", "R1", "R2", "R3", "R4", "R5"],
            decisions={
                "U2": "DUPLICATE_FETCH",
                "R1": "ALREADY_READ",
                "R2": "PROMPTED",
                "R3": "PROMPTED",
                "R4": "PROMPTED",
                "R5": "PROMPTED",
            },
            substances={
                "R1": "NEW_REVISION",
                "R2": "LAYOUT_ONLY",
                "R3": "MIRROR_REPOST",
                "R4": "REPEAT_FETCH",
                "R5": "NEW_REVISION",
            },
            cancelled={"R5": ["C-ROAD-AB"]},
        )
        diagnostics = ev.reopen_diagnostics(case, prediction)
        self.assertEqual(diagnostics["tp"], 1)
        self.assertEqual(diagnostics["fp"], 5)
        self.assertEqual(diagnostics["fn"], 1)
        counters = diagnostics["counters"]
        self.assertEqual(counters["already_read_repeat"], 1)
        self.assertEqual(counters["layout_only_false_alert"], 1)
        self.assertEqual(counters["repost_false_alert"], 1)
        self.assertEqual(counters["duplicate_fetch_false_alert"], 1)
        self.assertEqual(counters["post_cancel_prompt"], 1)
        self.assertEqual(counters["distinct_correction_wrongly_deduped"], 1)
        self.assertEqual(counters["other_false_alert"], 0)

    def test_a_miss_during_a_gold_source_gap_is_coverage_not_a_matching_defect(self):
        # The harness under-reports its own gap, but the gold says a source was
        # unreachable: the miss is coverage, not a matching defect.
        case, prediction = self.build(
            expected_ids=["U9"],
            predicted_ids=[],
            decisions={"U9": "NOT_YET_ACQUIRED"},
            gold_gap_kinds=["CONNECTION_FAILURE"],
        )
        diagnostics = ev.reopen_diagnostics(case, prediction)
        self.assertEqual(diagnostics["counters"]["missed_while_source_gap"], 1)
        self.assertEqual(diagnostics["counters"]["distinct_correction_wrongly_deduped"], 0)
        self.assertEqual(diagnostics["counters"]["missed_by_matching_rule"], 0)

    def test_a_miss_with_a_recorded_reason_is_not_reported_as_unrecorded(self):
        case, prediction = self.build(
            expected_ids=["U9", "U8"],
            predicted_ids=[],
            decisions={"U9": "OUTSIDE_TRACKED_WINDOW", "U8": "DATE_UNVERIFIED"},
        )
        counters = ev.reopen_diagnostics(case, prediction)["counters"]
        self.assertEqual(counters["missed_by_matching_rule"], 2)
        self.assertEqual(counters["missed_without_recorded_reason"], 0)
        self.assertEqual(counters["distinct_correction_wrongly_deduped"], 0)

    def test_a_miss_with_no_recorded_reason_is_still_flagged(self):
        case, prediction = self.build(expected_ids=["U9"], predicted_ids=[], decisions={})
        counters = ev.reopen_diagnostics(case, prediction)["counters"]
        self.assertEqual(counters["missed_without_recorded_reason"], 1)

    def test_multi_condition_hits_never_raise_the_true_positive_count(self):
        case, prediction = self.build(expected_ids=["U1"], predicted_ids=["U1"], decisions={})
        prediction["multi_condition_update_ids"] = ["U1"]
        single = ev.reopen_diagnostics(case, prediction)
        self.assertEqual(single["tp"], 1)
        self.assertEqual(single["multi_condition_prompt_count"], 1)
        inflated = dict(prediction)
        inflated["update_ids"] = ["U1", "U1"]
        self.assertEqual(ev.reopen_diagnostics(case, inflated)["tp"], 1)

    def test_a_malformed_prediction_fails_closed(self):
        case, prediction = self.build(expected_ids=["U1"], predicted_ids=["U1"], decisions={})
        broken = dict(prediction)
        broken["update_ids"] = "U1"
        with self.assertRaisesRegex(ValueError, "must be string array"):
            ev.reopen_diagnostics(case, broken)
        broken = dict(prediction)
        broken["decision_by_update_id"] = {"U1": 7}
        with self.assertRaisesRegex(ValueError, "string map"):
            ev.reopen_diagnostics(case, broken)


class CostLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = json.loads((DATA_DIR / "cost-ledger.json").read_text(encoding="utf-8"))
        self.manifest, _ = gold_cases()

    def test_human_time_and_model_spend_stay_not_run(self):
        statuses = {(row["arm"], row["unit"]): row["status"] for row in self.ledger["records"]}
        self.assertEqual(statuses[("C_v6", "human_hour")], "NOT_RUN")
        self.assertEqual(statuses[("A_v1", "human_hour")], "NOT_RUN")
        self.assertEqual(statuses[("B_v6", "human_hour")], "NOT_RUN")
        self.assertEqual(statuses[("C_v6", "model_request")], "NOT_APPLICABLE_NO_MODEL_CALL")
        self.assertIsNone(self.ledger["records"][0]["measured"])

    def test_cost_is_reported_per_query_update_and_condition(self):
        units = {row["unit"] for row in self.ledger["records"] if row["arm"] == "C_v6"}
        self.assertEqual(
            units, {"completed_query", "effective_update", "tracked_condition", "model_request", "human_hour"}
        )
        counts = {row["unit"]: row["count"] for row in self.ledger["records"] if row["arm"] == "C_v6"}
        self.assertEqual(counts["completed_query"], 9)
        self.assertEqual(counts["tracked_condition"], 2)

    def test_ledger_counts_agree_with_the_replay_receipt(self):
        receipt = run_arm("C_v6")["receipt"]["cost_scope"]
        counts = {
            (row["unit"]): row["count"]
            for row in self.ledger["records"]
            if row["arm"] == "C_v6"
        }
        self.assertEqual(counts["completed_query"], receipt["queries_completed"])
        self.assertEqual(counts["effective_update"], receipt["effective_updates"])
        self.assertEqual(counts["tracked_condition"], receipt["tracked_conditions"])
        self.assertEqual(counts["model_request"], receipt["model_requests"])
        # The prompted count is a different number and must not be confused with it.
        prompted = run_arm("C_v6")["receipt"]["counters"]["prompted_updates"]
        self.assertNotEqual(counts["effective_update"], prompted)

    def test_planned_scale_is_declared_beside_the_actual_counts(self):
        planned = self.manifest["planned_scale"]
        self.assertEqual(planned["planned_events"], 20)
        self.assertEqual(planned["planned_source_documents"], 60)
        self.assertLess(planned["actual_events"], planned["planned_events"])
        self.assertLess(planned["actual_source_documents"], planned["planned_source_documents"])
        self.assertTrue(planned["missing_samples_reason"])

    def test_actual_scale_counts_are_derivable_from_the_scenario(self):
        scenario = replay.load_scenario(DATA_DIR / "session.json")
        planned = self.manifest["planned_scale"]
        documents = {
            update["document_id"] for update in scenario["updates"] if not update.get("upstream_update_id")
        }
        self.assertEqual(planned["actual_events"], len(documents))
        self.assertEqual(
            planned["actual_source_documents"], len({update["source_id"] for update in scenario["updates"]})
        )
        self.assertEqual(planned["actual_reopen_points"], len(scenario["reopen_points"]))
        self.assertIn("document_id", planned["actual_definition"])


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.annotations = json.loads((DATA_DIR / "annotations.json").read_text(encoding="utf-8"))

    def test_model_output_is_never_its_own_gold(self):
        self.assertIs(self.annotations["model_output_used_as_gold"], False)
        self.assertEqual(self.annotations["disputes"], [])

    def test_split_matches_the_manifest(self):
        manifest, cases = gold_cases()
        self.assertEqual(self.annotations["split"]["development_reopen_ids"], manifest["development_reopen_ids"])
        self.assertEqual(self.annotations["split"]["heldout_reopen_ids"], manifest["heldout_reopen_ids"])
        self.assertEqual(
            self.annotations["split"]["development_reopen_ids"] + self.annotations["split"]["heldout_reopen_ids"],
            [case["case_id"] for case in cases],
        )


class V1RegressionTests(unittest.TestCase):
    """The v6 extension must not rewrite the frozen v1 harness behaviour."""

    def test_v1_self_check_still_passes(self):
        ev.self_check()

    def test_v1_dataset_is_untouched(self):
        manifest, cases = ev.load_gold()
        self.assertEqual(manifest["dataset_id"], "govintel-gold-v1")
        self.assertEqual(manifest["dataset_type"], "SYNTHETIC_REGRESSION_SEED")
        self.assertEqual(len(cases), 15)
        report = ev.evaluate(manifest, cases, ev.perfect_predictions(cases))
        # The v6 task is additive: v1 cases produce no reopen rows and no zeros
        # dressed up as scores.
        self.assertEqual(report["reopen_unread_rows"], [])
        self.assertEqual(report["reopen_unread"]["reopen_denominator"], 0)
        self.assertIsNone(report["reopen_unread"]["precision"])
        self.assertEqual(report["not_run"], [])


if __name__ == "__main__":
    unittest.main()