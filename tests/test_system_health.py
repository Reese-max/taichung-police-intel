import importlib.util
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "system-health.py"
spec = importlib.util.spec_from_file_location("system_health", SCRIPT)
health = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(health)


def stage(lane, name, outcome, **extra):
    return {"lane": lane, "stage": name, "outcome": outcome, **extra}


def complete_publication(overrides=None):
    rows = [
        stage("publication", "collection", "SUCCESS"),
        stage("publication", "canonical_validation", "SUCCESS"),
        stage("publication", "deployment", "SUCCESS"),
        stage("publication", "public_http_verification", "SUCCESS"),
    ]
    for name, outcome in (overrides or {}).items():
        for row in rows:
            if row["stage"] == name:
                row["outcome"] = outcome
    return rows


def healthy_chain():
    """A fully green end-to-end chain: every stage in the versioned model."""
    return [stage(row["lane"], row["stage"], "SUCCESS") for row in health.stage_model()["stages"]]


def chain_with_failure(error_class):
    """Same healthy chain, with exactly one stage forced into the given failure."""
    receipt = health.failure_stage_receipt(error_class)
    rows = healthy_chain()
    for row in rows:
        if (row["lane"], row["stage"]) == (receipt["lane"], receipt["stage"]):
            row["outcome"] = receipt["outcome"]
            row["error_class"] = receipt["error_class"]
    return rows


def load_module(name, path):
    module_spec = importlib.util.spec_from_file_location(name, path)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def controlled_publication_inputs():
    """Keep state-machine tests independent of the scheduled live collection."""
    status = json.loads(health.DEFAULT_STATUS.read_text(encoding="utf-8"))
    brief = json.loads(health.DEFAULT_BRIEF.read_text(encoding="utf-8"))
    run = status["latest_collection_run"]
    run["collection_run_id"] = "TEST-COLLECTION-RUN"
    run["status"] = "SUCCEEDED"
    for source in status["sources"]:
        source["source_health"] = "PASS"
        source["window_completeness"] = "COMPLETE_ZERO"
        source["freshness_status"] = "FRESH"
        source["last_success_at"] = "2026-09-11T08:23:26+08:00"
    brief["publication_status"] = "READY"
    brief["snapshot_complete"] = True
    brief["source_collection_run_id"] = run["collection_run_id"]
    brief["source_status_generated_at"] = status["generated_at"]
    return status, brief


class SystemHealthTests(unittest.TestCase):
    def test_unchanged_health_receipt_keeps_windows_checkout_timestamp(self):
        text = '{\n  "overall": "UNKNOWN"\n}\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "system-health.json"
            windows_bytes = text.replace("\n", "\r\n").encode("utf-8")
            path.write_bytes(windows_bytes)
            os.utime(path, (946684800, 946684800))
            before = path.stat().st_mtime_ns
            self.assertFalse(health.write_if_changed(path, text))
            self.assertEqual(path.read_bytes(), windows_bytes)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_query_failure_does_not_rewrite_publication_truth(self):
        stages = complete_publication() + [stage("query", "query_index", "FAILED", error_class="QUERY_INDEX_BUILD_FAILED")]
        result = health.build_health(stages)
        self.assertEqual(result["lanes"]["publication"], "HEALTHY")
        self.assertEqual(result["lanes"]["query"], "BLOCKED")
        self.assertEqual(result["overall"], "DEGRADED")
        self.assertTrue(result["operator_summary"]["requires_attention"])
        self.assertEqual(result["operator_summary"]["primary_stage"]["stage"], "query_index")
        self.assertEqual(result["operator_summary"]["primary_stage"]["error_class"], "QUERY_INDEX_BUILD_FAILED")
        self.assertTrue(all("last_success_at" in row for row in result["stages"]))

    def test_publish_or_deploy_failure_blocks_publication_lane(self):
        result = health.build_health(complete_publication({"deployment": "FAILED"}))
        self.assertEqual(result["lanes"]["publication"], "BLOCKED")
        self.assertEqual(result["overall"], "BLOCKED")

    def test_missing_required_publication_stage_never_reports_healthy(self):
        result = health.build_health([stage("publication", "collection", "SUCCESS")])
        self.assertEqual(result["lanes"]["publication"], "UNKNOWN")
        self.assertEqual(result["overall"], "UNKNOWN")

    def test_partial_collection_is_not_reported_as_zero_or_healthy(self):
        result = health.build_health(complete_publication({"collection": "PARTIAL"}))
        self.assertEqual(result["lanes"]["publication"], "PARTIAL")
        self.assertEqual(result["overall"], "PARTIAL")

    def test_unknown_deployment_receipt_keeps_publication_unknown(self):
        result = health.build_health(complete_publication({"deployment": "UNKNOWN"}))
        self.assertEqual(result["lanes"]["publication"], "UNKNOWN")
        self.assertEqual(result["overall"], "UNKNOWN")

    def test_latency_metrics_require_reliable_aware_ordered_times(self):
        result = health.build_health([], {
            "source_published_at": "2026-09-17T00:00:00+08:00",
            "detected_at": "2026-09-17T00:05:00+08:00",
            "verified_at": "2026-09-17T00:08:00+08:00",
            "published_at": "2026-09-17T00:10:00+08:00",
            "public_visible_at": "2026-09-17T00:11:00+08:00",
        })
        self.assertEqual(result["latency_metrics"]["source_to_detect_ms"], 300000)
        self.assertEqual(result["latency_metrics"]["detect_to_verify_ms"], 180000)
        self.assertEqual(result["latency_metrics"]["verify_to_publish_ms"], 120000)
        self.assertEqual(result["latency_metrics"]["publish_to_visible_ms"], 60000)
        self.assertIsNone(health.latency_ms("2026-09-17T00:00:00", "2026-09-17T00:01:00"))

    def test_duplicate_stage_receipts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate stage"):
            health.build_health([
                stage("publication", "deployment", "SUCCESS"),
                stage("publication", "deployment", "FAILED"),
            ])

    def test_bad_stage_time_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid ended_at"):
            health.build_health([stage("publication", "collection", "SUCCESS", ended_at="not-a-time")])

    def test_succeeded_run_with_empty_sources_is_partial_not_success(self):
        status, brief = controlled_publication_inputs()
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        status["sources"] = []
        stages = health.current_publication_stages(status, brief)
        collection = next(row for row in stages if row["stage"] == "collection")
        self.assertEqual(collection["outcome"], "PARTIAL")
        self.assertEqual(collection["error_class"], "SOURCE_COVERAGE_OR_COLLECTION_GAP")

    def test_missing_approved_source_has_a_counted_gap_and_operator_action(self):
        status, brief = controlled_publication_inputs()
        status["sources"] = [row for row in status["sources"] if row["source_id"] != "S-029"]
        before = deepcopy(status)
        stages = health.current_publication_stages(status, brief)
        collection = next(row for row in stages if row["stage"] == "collection")
        self.assertEqual(collection["outcome"], "PARTIAL")
        self.assertEqual(collection["error_class"], "SOURCE_COVERAGE_OR_COLLECTION_GAP")
        self.assertEqual(collection["item_count"], len(status["sources"]))
        self.assertEqual(collection["gap_count"], 1)
        self.assertIsNone(collection["last_success_at"])
        self.assertEqual(len(collection["source_warnings"]), 1)
        warning = collection["source_warnings"][0]
        self.assertEqual(warning["source_id"], "S-029")
        self.assertEqual(warning["receipt_status"], "MISSING")
        self.assertEqual(warning["source_health"], "UNKNOWN")
        self.assertEqual(warning["reasons"], ["SOURCE_COVERAGE_OR_COLLECTION_GAP"])
        self.assertIn("核准來源", warning["next_actions"][0])
        result = health.build_health(stages, context={"sources": status["sources"]})
        self.assertEqual(result["operator_summary"]["source_actions"], [
            {key: warning[key] for key in ("source_id", "reasons", "next_actions")}
        ])
        self.assertEqual(result["lanes"]["publication"], "PARTIAL")
        self.assertEqual(status, before)

    def test_missing_source_and_failed_or_stale_present_sources_keep_all_gaps(self):
        status, brief = controlled_publication_inputs()
        status["sources"] = [row for row in status["sources"] if row["source_id"] != "S-029"]
        failed_id, stale_id = [row["source_id"] for row in status["sources"][:2]]
        status["sources"][0].update(source_health="FAILED", window_completeness="PARTIAL_WINDOW")
        status["sources"][1]["freshness_status"] = "STALE"
        collection = next(row for row in health.current_publication_stages(status, brief) if row["stage"] == "collection")
        self.assertEqual(collection["outcome"], "PARTIAL")
        self.assertEqual(collection["gap_count"], 2)
        warnings = {row["source_id"]: row for row in collection["source_warnings"]}
        self.assertEqual(set(warnings), {"S-029", failed_id, stale_id})
        self.assertEqual(warnings[failed_id]["source_health"], "FAILED")
        self.assertEqual(warnings[stale_id]["reasons"], ["SOURCE_FRESHNESS_STALE"])
        self.assertIsNone(collection["last_success_at"])

    def test_invalid_last_success_time_is_unknown_without_timezone_or_generation_substitution(self):
        for value in ("2026-09-11T08:23:26", "not-a-time", None):
            with self.subTest(value=value):
                status, brief = controlled_publication_inputs()
                source_id = status["sources"][0]["source_id"]
                status["sources"][0]["last_success_at"] = value
                before = deepcopy(status)
                stages = health.current_publication_stages(status, brief)
                collection = next(row for row in stages if row["stage"] == "collection")
                self.assertEqual(collection["outcome"], "UNKNOWN")
                self.assertEqual(collection["error_class"], "SOURCE_LAST_SUCCESS_UNKNOWN")
                self.assertIsNone(collection["last_success_at"])
                self.assertIsNone(health.aggregate_last_success_at(status["sources"]))
                self.assertEqual(collection["gap_count"], 0)
                warnings = collection["source_warnings"]
                self.assertEqual(len(warnings), 1)
                self.assertEqual(warnings[0]["source_id"], source_id)
                self.assertEqual(warnings[0]["reasons"], ["SOURCE_LAST_SUCCESS_UNKNOWN"])
                self.assertIn("時區", warnings[0]["next_actions"][0])
                result = health.build_health(stages)
                self.assertEqual(result["lanes"]["publication"], "UNKNOWN")
                self.assertEqual(len(result["operator_summary"]["source_actions"]), 1)
                self.assertEqual(status, before)

    def test_stale_brief_from_another_run_is_not_validation_success(self):
        status, brief = controlled_publication_inputs()
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        brief["publication_status"] = "READY"
        brief["snapshot_complete"] = True
        brief["source_collection_run_id"] = "different-run"
        stages = health.current_publication_stages(status, brief)
        validation = next(row for row in stages if row["stage"] == "canonical_validation")
        self.assertEqual(validation["outcome"], "PARTIAL")
        self.assertEqual(validation["error_class"], "PUBLICATION_GENERATION_MISMATCH")

    def test_source_freshness_controls_collection_health(self):
        for freshness, expected in {
            "FRESH": "SUCCESS",
            "RECENT": "SUCCESS",
            "STALE": "STALE",
            "VERY_STALE": "STALE",
            "NO_DATA": "UNKNOWN",
            "UNKNOWN": "UNKNOWN",
        }.items():
            with self.subTest(freshness=freshness):
                status, brief = controlled_publication_inputs()
                status["latest_collection_run"]["status"] = "SUCCEEDED"
                for source in status["sources"]:
                    source["freshness_status"] = freshness
                collection = next(
                    row for row in health.current_publication_stages(status, brief)
                    if row["stage"] == "collection"
                )
                self.assertEqual(collection["outcome"], expected)

    def test_collection_uses_source_policy_freshness_normalization(self):
        status, brief = controlled_publication_inputs()
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        for source in status["sources"]:
            source["freshness_status"] = " stale "
        collection = next(
            row for row in health.current_publication_stages(status, brief)
            if row["stage"] == "collection"
        )
        self.assertEqual(collection["outcome"], "STALE")

    def test_acquisition_gap_takes_priority_without_erasing_per_source_date_warnings(self):
        for freshness in ("STALE", "UNKNOWN"):
            with self.subTest(freshness=freshness):
                status, brief = controlled_publication_inputs()
                failed_id = status["sources"][0]["source_id"]
                date_id = status["sources"][1]["source_id"]
                status["sources"][0].update(source_health="FAILED", window_completeness="PARTIAL_WINDOW")
                status["sources"][1]["freshness_status"] = freshness
                before = deepcopy(status)
                stages = health.current_publication_stages(status, brief)
                collection = next(row for row in stages if row["stage"] == "collection")
                self.assertEqual(collection["outcome"], "PARTIAL")
                self.assertEqual(collection["error_class"], "SOURCE_COVERAGE_OR_COLLECTION_GAP")
                self.assertEqual(collection["gap_count"], 1)
                self.assertEqual(collection["last_success_at"], health.aggregate_last_success_at(status["sources"]))
                warnings = {row["source_id"]: row for row in collection["source_warnings"]}
                self.assertEqual(set(warnings), {failed_id, date_id})
                self.assertEqual(warnings[failed_id]["reasons"], ["SOURCE_COVERAGE_OR_COLLECTION_GAP"])
                self.assertEqual(warnings[date_id]["reasons"], [f"SOURCE_FRESHNESS_{freshness}"])
                self.assertEqual(warnings[failed_id]["source_health"], "FAILED")
                self.assertEqual(warnings[failed_id]["window_completeness"], "PARTIAL_WINDOW")
                self.assertIn("保留 LKG", warnings[failed_id]["next_actions"][0])
                self.assertIn("官方", warnings[date_id]["next_actions"][0])
                self.assertEqual(status, before)

                result = health.build_health(stages, context={"sources": status["sources"]})
                actions = {row["source_id"]: row for row in result["operator_summary"]["source_actions"]}
                self.assertEqual(result["lanes"]["publication"], "PARTIAL")
                self.assertEqual(actions[failed_id]["reasons"], warnings[failed_id]["reasons"])
                self.assertEqual(actions[date_id]["reasons"], warnings[date_id]["reasons"])
                self.assertEqual(actions[date_id]["next_actions"], warnings[date_id]["next_actions"])
                stale_ratio = result["slo"]["metrics"]["stale_source_ratio"]["value"]
                self.assertEqual(stale_ratio, 1 / len(status["sources"]) if freshness == "STALE" else 0)

    def test_failed_stale_source_retains_both_reasons_and_actions(self):
        status, brief = controlled_publication_inputs()
        status["sources"][0].update(source_health="FAILED", window_completeness="PARTIAL_WINDOW", freshness_status="STALE")
        collection = next(row for row in health.current_publication_stages(status, brief) if row["stage"] == "collection")
        self.assertEqual(collection["outcome"], "PARTIAL")
        self.assertEqual(len(collection["source_warnings"]), 1)
        warning = collection["source_warnings"][0]
        self.assertEqual(warning["reasons"], ["SOURCE_COVERAGE_OR_COLLECTION_GAP", "SOURCE_FRESHNESS_STALE"])
        self.assertEqual(len(warning["next_actions"]), 2)

    def test_fresh_complete_sources_have_no_source_attention_actions(self):
        status, brief = controlled_publication_inputs()
        stages = health.current_publication_stages(status, brief)
        collection = next(row for row in stages if row["stage"] == "collection")
        self.assertEqual(collection["outcome"], "SUCCESS")
        self.assertEqual(collection["source_warnings"], [])
        self.assertEqual(health.build_health(stages)["operator_summary"]["source_actions"], [])

    def test_collection_keeps_last_known_success_when_current_sources_are_stale(self):
        status, brief = controlled_publication_inputs()
        for source in status["sources"]:
            source["freshness_status"] = "STALE"
        collection = next(
            row for row in health.current_publication_stages(status, brief)
            if row["stage"] == "collection"
        )
        self.assertEqual(collection["outcome"], "STALE")
        self.assertEqual(collection["last_success_at"], health.aggregate_last_success_at(status["sources"]))

    def test_collection_last_success_stays_unknown_when_any_source_lacks_it(self):
        status, brief = controlled_publication_inputs()
        status["latest_collection_run"]["status"] = "PARTIAL"
        status["sources"][0].pop("last_success_at", None)
        collection = next(
            row for row in health.current_publication_stages(status, brief)
            if row["stage"] == "collection"
        )
        self.assertIsNone(collection["last_success_at"])

    def test_stale_source_cannot_become_healthy_with_complete_publication_receipt(self):
        status, brief = controlled_publication_inputs()
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        for source in status["sources"]:
            source["freshness_status"] = "STALE"
        stages = health.current_publication_stages(status, brief)
        stages = [row for row in stages if row["stage"] not in {"deployment", "public_http_verification"}]
        stages.extend([
            stage("publication", "deployment", "SUCCESS"),
            stage("publication", "public_http_verification", "SUCCESS"),
        ])
        result = health.build_health(stages)
        self.assertEqual(result["lanes"]["publication"], "STALE")
        self.assertNotEqual(result["overall"], "HEALTHY")

    def test_no_data_source_cannot_become_healthy_with_complete_publication_receipt(self):
        status, brief = controlled_publication_inputs()
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        for source in status["sources"]:
            source["freshness_status"] = "NO_DATA"
        stages = health.current_publication_stages(status, brief)
        stages = [row for row in stages if row["stage"] not in {"deployment", "public_http_verification"}]
        stages.extend([
            stage("publication", "deployment", "SUCCESS"),
            stage("publication", "public_http_verification", "SUCCESS"),
        ])
        result = health.build_health(stages)
        self.assertEqual(result["lanes"]["publication"], "UNKNOWN")
        self.assertNotEqual(result["overall"], "HEALTHY")

    def test_checked_in_current_artifacts_produce_machine_readable_stage_breakdown(self):
        if os.getenv("GOVINTEL_PUBLICATION_WORKFLOW") == "1":
            self.skipTest("Pages publication tests use generated artifacts, not the checked-in snapshot")
        result = health.load_current()
        self.assertEqual(result["schema_version"], 1)
        names = {(row["lane"], row["stage"]) for row in result["stages"]}
        self.assertIn(("publication", "collection"), names)
        self.assertIn(("publication", "canonical_validation"), names)
        self.assertIn(("publication", "deployment"), names)
        self.assertIn(("publication", "public_http_verification"), names)
        self.assertIn(("query", "query_index"), names)
        # Existing checked-in artifacts are stale and still lack deployment/HTTP
        # receipts; the model must not manufacture HEALTHY.
        self.assertEqual(result["lanes"]["publication"], "STALE")
        self.assertEqual(result["operator_summary"]["status"], "STALE")
        self.assertTrue(result["operator_summary"]["requires_attention"])
        self.assertEqual(result["policy"]["active_source_ids"], sorted(health.load_current_policy()["active_source_ids"]))

    def test_schema_contract_drift_is_explicit_discovery_failure(self):
        good = health.schema_contract_stage({"overall": "HEALTHY", "sources": [{"status": "NO_DRIFT"}], "review_inbox": []})
        self.assertEqual(good["outcome"], "SUCCESS")
        broken = health.schema_contract_stage({
            "overall": "BLOCKED",
            "sources": [{"status": "BREAKING_DRIFT"}],
            "review_inbox": [{"source_id": "S-007"}],
        })
        self.assertEqual((broken["outcome"], broken["error_class"], broken["review_inbox_count"]), ("FAILED", "SOURCE_CONTRACT_DRIFT", 1))

    def test_schema_drift_candidates_reach_public_review_projection(self):
        receipt = {
            "generated_at": "2026-09-21T00:00:00+00:00",
            "review_inbox": [{
                "source_id": "S-007",
                "status": "SOURCE_UNAVAILABLE",
                "reasons": ["HTTP_503"],
                "observed_at": "2026-09-21T00:00:00+00:00",
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            rows = health.load_review_inbox(Path(directory) / "review.json", receipt)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "PARTIAL_SOURCE")
        self.assertNotIn("evidence", rows[0])
        self.assertNotIn("audit", rows[0])

    # ------------------------------------------------------------------
    # Issue #35 — end-to-end observability / SLO measurement fields.
    # ------------------------------------------------------------------

    def test_stage_model_is_versioned_and_covers_the_end_to_end_chain(self):
        self.assertEqual(health.STAGE_MODEL_VERSION, "govintel-e2e-stages.v1")
        model = health.stage_model()
        self.assertEqual(model["version"], "govintel-e2e-stages.v1")
        self.assertTrue(model["stage_ids"])
        for expected in (
            "source_contracts",
            "upstream_operating_state",
            "collection",
            "parsing",
            "fusion_verification",
            "canonical_validation",
            "publication_build",
            "deployment",
            "public_http_verification",
"query_index",
            "read_only_mcp",
            "mcp_web_query",
            "upstream_operating_state",
        ):
            self.assertIn(expected, model["stage_ids"])
        orders = [row["order"] for row in model["stages"]]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(orders), len(set(orders)))
        for row in model["stages"]:
            self.assertIn(row["lane"], health.VALID_LANES)
            self.assertTrue(row["description"])
            self.assertEqual(health.stage_model_entry(row["stage"])["lane"], row["lane"])
        self.assertIsNone(health.stage_model_entry("not-a-stage"))

    def test_every_stage_carries_last_success_and_generation_hash_linkage(self):
        result = health.build_health(healthy_chain())
        self.assertEqual(len(result["stages"]), len(health.stage_model()["stage_ids"]))
        for row in result["stages"]:
            for field in (
                "lane",
                "stage",
                "outcome",
                "started_at",
                "ended_at",
                "last_success_at",
                "generation_id",
                "upstream_hash",
                "downstream_hash",
                "item_count",
                "gap_count",
                "error_stage",
                "error_class",
                "receipt_ref",
            ):
                self.assertIn(field, row, f"{row['stage']} missing {field}")
            self.assertIsNone(row["error_stage"])
        failed = health.build_health(chain_with_failure("DEPLOY_ACTION_FAILED"))
        deployment = next(row for row in failed["stages"] if row["stage"] == "deployment")
        self.assertEqual(deployment["error_stage"], "deployment")
        self.assertIsNone(next(row for row in failed["stages"] if row["stage"] == "collection")["error_stage"])

    def test_failed_stage_reports_the_error_stage_that_owns_it(self):
        result = health.build_health(chain_with_failure("DEPLOY_ACTION_FAILED"))
        deployment = next(row for row in result["stages"] if row["stage"] == "deployment")
        self.assertEqual(deployment["error_stage"], "deployment")
        collection = next(row for row in result["stages"] if row["stage"] == "collection")
        self.assertIsNone(collection["error_stage"])

    # Every simulated critical failure must land in exactly one stage, so an
    # operator can answer "which link of the chain broke" without guessing.
    FAILURE_MATRIX = {
        "COLLECTOR_TRANSPORT_ERROR": ("publication", "collection", "FAILED", "BLOCKED", "BLOCKED"),
        "PARSER_OUTPUT_PARTIAL": ("publication", "parsing", "PARTIAL", "PARTIAL", "PARTIAL"),
        "FUSION_VERIFICATION_FAILED": ("publication", "fusion_verification", "FAILED", "BLOCKED", "BLOCKED"),
        "PROTECTED_BRANCH_PUSH_REJECTED": ("publication", "deployment", "FAILED", "BLOCKED", "BLOCKED"),
        "DEPLOY_ACTION_FAILED": ("publication", "deployment", "FAILED", "BLOCKED", "BLOCKED"),
        "PUBLIC_HTTP_HASH_MISMATCH": ("publication", "public_http_verification", "FAILED", "BLOCKED", "BLOCKED"),
        "QUERY_INDEX_BUILD_FAILED": ("query", "query_index", "FAILED", "BLOCKED", "DEGRADED"),
        "MCP_RUNTIME_UNAVAILABLE": ("query", "mcp_web_query", "FAILED", "BLOCKED", "DEGRADED"),
        "SOURCE_CONTRACT_DRIFT": ("discovery", "source_contracts", "FAILED", "BLOCKED", "DEGRADED"),
    }

    def test_each_failure_mode_lands_in_exactly_one_stage(self):
        for error_class, (lane, name, outcome, lane_health, overall) in self.FAILURE_MATRIX.items():
            with self.subTest(error_class=error_class):
                receipt = health.failure_stage_receipt(error_class)
                self.assertEqual((receipt["lane"], receipt["stage"]), (lane, name))
                self.assertEqual(receipt["outcome"], outcome)
                self.assertEqual(receipt["error_class"], error_class)
                self.assertEqual(receipt["error_stage"], name)
                owners = [
                    row["stage"]
                    for row in health.stage_model()["stages"]
                    if error_class in health.error_classes_for_stage(row["stage"])
                ]
                self.assertEqual(owners, [name])
                result = health.build_health(chain_with_failure(error_class))
                self.assertEqual(result["lanes"][lane], lane_health)
                self.assertEqual(result["overall"], overall)
                self.assertEqual(result["operator_summary"]["primary_stage"]["stage"], name)
                attributed = next(
                    row for row in result["stages"]
                    if (row["lane"], row["stage"]) == (lane, name)
                )
                self.assertEqual(attributed["error_class"], error_class)

    def test_publish_failure_never_reports_a_collection_failure(self):
        result = health.build_health(chain_with_failure("PROTECTED_BRANCH_PUSH_REJECTED"))
        collection = next(row for row in result["stages"] if row["stage"] == "collection")
        self.assertEqual(collection["outcome"], "SUCCESS")
        self.assertIsNone(collection["error_class"])
        self.assertEqual(result["operator_summary"]["primary_stage"]["stage"], "deployment")
        self.assertEqual(result["operator_summary"]["primary_stage"]["error_class"], "PROTECTED_BRANCH_PUSH_REJECTED")

    def test_unknown_error_class_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unclassified error class"):
            health.failure_stage_receipt("SOMETHING_WE_NEVER_DEFINED")

    def test_every_error_class_this_repo_emits_is_classified(self):
        """No emitted error class may be invisible to the attribution registry."""
        seen = set()

        def collect(stages):
            for row in stages:
                if row.get("error_class"):
                    seen.add(row["error_class"])

        status, brief = controlled_publication_inputs()
        collect(health.current_publication_stages(status, brief))
        collect(health.current_publication_stages(status, brief, None, None))
        status["latest_collection_run"]["status"] = "FAILED"
        collect(health.current_publication_stages(status, brief))
        collect([health.upstream_operating_state_stage(
            {"upstream_operating_state": "PAUSED", "upstream_current": False})])

        outcome = load_module("publication_outcome", ROOT / "scripts" / "publication-outcome.py")
        for env in (
            {},
            {"COLLECT": "skipped", "DEPLOY_RESULT": "success", "PUBLIC_VERIFY": "success"},
            {"COLLECT": "success", "DEPLOY_RESULT": "failure", "PUBLIC_VERIFY": "failure"},
            {"COLLECT": "success", "DEPLOY_RESULT": "success", "PUBLIC_VERIFY": "success",
             "QUERY_VERIFY": "failure"},
            {"COLLECT": "success", "SCHEMA_DRIFT": "success", "SCHEMA_DRIFT_OVERALL": "BLOCKED",
             "DEPLOY_RESULT": "success", "PUBLIC_VERIFY": "success"},
        ):
            receipt = outcome.runtime_health(env, observed_at="2026-09-11T08:00:00+00:00")
            collect(receipt["stages"])

        checkout = load_module("verify_current_checkout", ROOT / "scripts" / "verify-current-checkout.py")
        collect(health.current_publication_stages(status, brief))
        collect([{
            "lane": "query",
            "stage": row["stage"],
            "outcome": row["outcome"],
            "error_class": row.get("error_class"),
        } for row in [
            {"lane": "query", "stage": "query_index", "outcome": "FAILED", "error_class": "QUERY_CHECK_FAILED"},
            {"lane": "query", "stage": "read_only_mcp", "outcome": "FAILED", "error_class": "MCP_RUNTIME_NOT_VERIFIED"},
            {"lane": "query", "stage": "mcp_web_query", "outcome": "SKIPPED", "error_class": "CAPABILITY_NOT_AVAILABLE"},
        ]])
        self.assertIsNotNone(checkout)
        self.assertTrue(seen)
        unclassified = sorted(seen - set(health.FAILURE_STAGE_CLASSIFICATION))
        self.assertEqual(unclassified, [], f"unclassified error classes emitted: {unclassified}")
        for stage in health.stage_model()["stage_ids"]:
            self.assertEqual(
                [owner for owner in health.stage_model()["stages"] if owner["stage"] == stage],
                [health.stage_model_entry(stage)],
            )

    def test_same_instant_latency_stays_unknown_instead_of_zero(self):
        shared = "2026-09-11T08:00:00+08:00"
        result = health.build_health([
            stage("publication", "collection", "SUCCESS", ended_at=shared),
            stage("publication", "canonical_validation", "SUCCESS", ended_at=shared),
            stage("publication", "deployment", "SUCCESS", ended_at=shared),
            stage("publication", "public_http_verification", "SUCCESS", ended_at=shared),
        ])
        self.assertIsNone(result["latency_metrics"]["detect_to_verify_ms"])
        self.assertIsNone(result["latency_metrics"]["verify_to_publish_ms"])
        self.assertIsNone(result["latency_metrics"]["publish_to_visible_ms"])
        self.assertIsNone(result["slo"]["metrics"]["detect_to_verify_ms"]["value"])
        self.assertFalse(result["slo"]["metrics"]["detect_to_verify_ms"]["measured"])
        self.assertIsNone(health.latency_ms(shared, shared))
        self.assertIsNone(health.latency_ms(shared, "2026-09-11T08:00:00"))

    def test_stage_model_declares_the_stages_that_gate_a_healthy_verdict(self):
        model = health.stage_model()
        self.assertEqual(model["required_publication_stages"], sorted(health.REQUIRED_PUBLICATION_STAGES))
        # HEALTHY stays unreachable while a required publication stage is absent.
        self.assertEqual(
            health.build_health([stage("publication", "collection", "SUCCESS")])["overall"],
            "UNKNOWN",
        )
        result = health.load_current()
        self.assertEqual(
            result["stage_model"]["required_publication_stages"],
            sorted(health.REQUIRED_PUBLICATION_STAGES),
        )

    def test_upstream_operating_state_is_reflected_without_overriding_govintel_health(self):
        for state, outcome in {
            "ACTIVE": "SUCCESS",
            "DEGRADED": "PARTIAL",
            "RESTORING": "PARTIAL",
            "PAUSED": "STALE",
        }.items():
            with self.subTest(operating_state=state):
                stage = health.upstream_operating_state_stage(
                    {
                        "upstream_operating_state": state,
                        "upstream_current": state == "ACTIVE",
                        "last_seen_generation_id": "gdf-abc",
                        "last_seen_generated_at": "2026-09-11T08:23:26+08:00",
                        "last_seen_content_hash": "a" * 64,
                    }
                )
                self.assertEqual((stage["lane"], stage["stage"]), ("discovery", "upstream_operating_state"))
                self.assertEqual(stage["outcome"], outcome)
                self.assertEqual(stage["generation_id"], "gdf-abc")
                self.assertEqual(stage["upstream_hash"], "a" * 64)
                self.assertEqual(stage["last_success_at"], "2026-09-11T08:23:26+08:00" if state == "ACTIVE" else None)
                result = health.build_health(
                    [row for row in healthy_chain() if row["stage"] != "upstream_operating_state"],
                    context={
                        "upstream_receipt": {
                            "upstream_operating_state": state,
                            "upstream_current": state == "ACTIVE",
                            "last_seen_generation_id": "gdf-abc",
                            "last_seen_generated_at": "2026-09-11T08:23:26+08:00",
                        }
                    },
                )
                self.assertEqual(result["lanes"]["publication"], "HEALTHY")
                self.assertEqual(result["upstream"]["upstream_operating_state"], state)
                self.assertFalse(result["upstream"]["overrides_govintel_health"])
                self.assertEqual(result["overall"], "HEALTHY" if state == "ACTIVE" else "DEGRADED")

    def test_missing_upstream_operating_state_receipt_is_unknown_not_success(self):
        result = health.build_health(complete_publication())
        self.assertEqual(result["upstream"]["upstream_operating_state"], "UNKNOWN")
        self.assertEqual(result["upstream"]["overrides_govintel_health"], False)
        stage = next(row for row in result["stages"] if row["stage"] == "upstream_operating_state")
        self.assertEqual(stage["outcome"], "UNKNOWN")
        self.assertEqual(stage["error_class"], "NO_UPSTREAM_OPERATING_STATE_RECEIPT")
        self.assertEqual(result["overall"], "DEGRADED")

    def test_slo_metrics_stay_unknown_without_reliable_evidence(self):
        slo = health.slo_metrics([], {}, None, None)
        self.assertEqual(slo["status"], "MEASUREMENT_ONLY")
        self.assertIsNone(slo["thresholds"])
        self.assertEqual(sorted(slo["metrics"]), sorted(name for name, _ in health.SLO_METRIC_FIELDS))
        for name, metric in slo["metrics"].items():
            with self.subTest(metric=name):
                self.assertIsNone(metric["value"])
                self.assertFalse(metric["measured"])
                self.assertTrue(metric["reason"])

    def test_slo_metrics_measure_collection_source_and_publication_ratios(self):
        sources = [
            {"source_health": "PASS", "window_completeness": "COMPLETE_WITH_ITEMS", "freshness_status": "FRESH",
             "last_success_at": "2026-09-11T08:00:00+08:00", "data_as_of": "2026-09-11T08:00:00+08:00"},
            {"source_health": "PASS", "window_completeness": "COMPLETE_ZERO", "freshness_status": "FRESH",
             "last_success_at": "2026-09-11T08:10:00+08:00", "data_as_of": "2026-09-11T08:10:00+08:00"},
            {"source_health": "PASS", "window_completeness": "COMPLETE_WITH_ITEMS", "freshness_status": "STALE",
             "last_success_at": "2026-09-11T08:20:00+08:00", "data_as_of": "2026-09-11T08:20:00+08:00"},
            {"source_health": "FAILED", "window_completeness": "PARTIAL_WINDOW", "freshness_status": "NO_DATA",
             "last_success_at": "2026-09-11T08:21:00+08:00", "data_as_of": "2026-09-11T08:21:00+08:00"},
        ]
        metrics = health.slo_metrics(sources, {}, None, "2026-09-11T09:00:00+08:00",
                                 chain_with_failure("PUBLIC_HTTP_HASH_MISMATCH"))["metrics"]
        self.assertEqual(metrics["collection_success_ratio"]["value"], 0.75)
        self.assertEqual(metrics["stale_source_ratio"]["value"], 0.25)
        self.assertEqual(metrics["partial_source_ratio"]["value"], 0.25)
        self.assertEqual(metrics["publication_mismatch_count"]["value"], 1)
        self.assertEqual(metrics["source_freshness_age_ms"]["value"], 3600000)
        for name in ("collection_success_ratio", "stale_source_ratio", "partial_source_ratio",
                     "publication_mismatch_count", "source_freshness_age_ms"):
            self.assertTrue(metrics[name]["measured"], name)
        # A publication mismatch count must be independent of the collection ratios.
        healthy = health.slo_metrics(sources, {}, None, "2026-09-11T09:00:00+08:00", healthy_chain())["metrics"]
        self.assertEqual(healthy["publication_mismatch_count"]["value"], 0)
        self.assertEqual(healthy["collection_success_ratio"]["value"], 0.75)
        no_receipts = health.slo_metrics(sources, {}, None, "2026-09-11T09:00:00+08:00")["metrics"]
        self.assertFalse(no_receipts["publication_mismatch_count"]["measured"])

    def test_slo_query_metrics_need_a_query_receipt(self):
        without = health.slo_metrics([], {}, None, "2026-09-11T09:00:00+08:00")["metrics"]
        self.assertFalse(without["query_index_lag_ms"]["measured"])
        self.assertFalse(without["query_error_rate"]["measured"])
        with_receipt = health.slo_metrics([], {}, {
            "indexed_at": "2026-09-11T08:50:00+08:00",
            "published_at": "2026-09-11T08:40:00+08:00",
            "query_total": 200,
            "query_errors": 5,
        }, "2026-09-11T09:00:00+08:00")["metrics"]
        self.assertEqual(with_receipt["query_index_lag_ms"]["value"], 600000)
        self.assertEqual(with_receipt["query_error_rate"]["value"], 0.025)

    def test_latency_chain_is_derived_from_stage_timestamps_without_fabricating_source_time(self):
        stages = [
            stage("publication", "collection", "SUCCESS", ended_at="2026-09-11T08:00:00+08:00"),
            stage("publication", "parsing", "SUCCESS", ended_at="2026-09-11T08:01:00+08:00"),
            stage("publication", "canonical_validation", "SUCCESS", ended_at="2026-09-11T08:05:00+08:00"),
            stage("publication", "publication_build", "SUCCESS", ended_at="2026-09-11T08:10:00+08:00"),
            stage("publication", "deployment", "SUCCESS", ended_at="2026-09-11T08:12:00+08:00"),
            stage("publication", "public_http_verification", "SUCCESS", ended_at="2026-09-11T08:13:00+08:00"),
        ]
        result = health.build_health(stages)
        metrics = result["latency_metrics"]
        self.assertIsNone(metrics["source_to_detect_ms"])
        self.assertEqual(metrics["detect_to_verify_ms"], 300000)
        self.assertEqual(metrics["verify_to_publish_ms"], 420000)
        self.assertEqual(metrics["publish_to_visible_ms"], 60000)

    def test_checked_in_receipt_carries_versioned_model_and_unknown_safe_slo(self):
        if os.getenv("GOVINTEL_PUBLICATION_WORKFLOW") == "1":
            self.skipTest("Pages publication tests use generated artifacts, not the checked-in snapshot")
        first = health.load_current()
        second = health.load_current()
        self.assertEqual(first, second)
        self.assertEqual(first["stage_model"]["version"], health.STAGE_MODEL_VERSION)
        self.assertEqual(first["slo"]["status"], "MEASUREMENT_ONLY")
        self.assertIsNone(first["slo"]["thresholds"])
        self.assertTrue(first["slo"]["metrics"]["publish_to_public_visible_ms"]["measured"] is False)
        self.assertFalse(first["upstream"]["overrides_govintel_health"])
        self.assertIsNotNone(health.parse_time(first["generated_at"]))
        self.assertEqual(first["latency_metrics"]["publish_to_visible_ms"], None)
        names = {row["stage"] for row in first["stages"]}
        for expected in health.stage_model()["stage_ids"]:
            self.assertIn(expected, names)

    def test_successful_fetch_does_not_fill_an_unknown_official_data_date(self):
        sources = [{"last_success_at": "2026-09-11T08:59:00+08:00", "data_as_of": None}]
        receipt = health.slo_metrics(sources, generated_at="2026-09-11T09:00:00+08:00")
        self.assertIsNone(receipt["metrics"]["source_freshness_age_ms"]["value"])
        self.assertFalse(receipt["metrics"]["source_freshness_age_ms"]["measured"])

    def test_committed_receipt_is_reproducible_from_the_committed_inputs(self):
        """A stale or non-deterministic checked-in receipt must fail the suite.

        CI and `npm test` regenerate the artifact in place; if that output ever
        differs from what is committed, the receipt the site serves is stale.
        """
        if os.getenv("GOVINTEL_PUBLICATION_WORKFLOW") == "1":
            self.skipTest("Pages publication tests use generated artifacts, not the checked-in snapshot")
        if health.load_upstream_receipt() is not None or health.load_query_receipt() is not None:
            self.skipTest("local runtime receipts exist, so the receipt is not the checked-in one")
        committed = (health.ROOT / "apps/web/public/data/system-health.json").read_text(encoding="utf-8")
        result = health.load_current()
        self.assertEqual(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            committed,
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "system-health.json"
            self.assertTrue(health.write_if_changed(target, committed))
            self.assertFalse(health.write_if_changed(target, committed))
            self.assertEqual(target.read_text(encoding="utf-8"), committed)

    def test_source_health_candidates_reach_public_review_projection(self):
        status = {
            "generated_at": "2026-09-21T00:00:00+00:00",
            "sources": [
                {
                    "source_id": "S-004",
                    "source_health": "PASS",
                    "window_completeness": "COMPLETE_ZERO",
                    "freshness_status": "STALE",
                    "last_checked_at": "2026-09-21T00:00:00+00:00",
                    "current_source_run_id": "run-4",
                },
                {
                    "source_id": "S-006",
                    "source_health": "PASS",
                    "window_completeness": "COMPLETE_WITH_ITEMS",
                    "freshness_status": "FRESH",
                    "last_checked_at": "2026-09-21T00:00:00+00:00",
                    "current_source_run_id": "run-6",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            rows = health.load_review_inbox(Path(directory) / "review.json", source_status=status)
        self.assertEqual([row["reason"] for row in rows], ["STALE_SOURCE"])
        self.assertEqual(rows[0]["entity_ids"], {"source_id": "S-004"})
        self.assertNotIn("evidence", rows[0])
        self.assertNotIn("audit", rows[0])


if __name__ == "__main__":
    unittest.main()
