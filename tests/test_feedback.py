import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "feedback.py"
spec = importlib.util.spec_from_file_location("feedback_cli", SCRIPT)
cli = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cli)
from intel_v2 import feedback as model


def make_feedback(state=None, **overrides):
    values = {
        "target_type": "EVENT",
        "target_id": "PE-1",
        "target_version": "v1",
        "reason": "FALSE_MERGE",
        "original_output_sha256": "a" * 64,
        "corrected_expected_state": {"same_event": False},
        "evidence_refs": ["S-036#fixture"],
        "linked_review_id": "REVIEW-1",
        "created_at": "2026-09-21T00:00:00+08:00",
        "model_version": "model:test",
        "parser_version": "parser:test",
        "registry_hash": "registry:test",
    }
    values.update(overrides)
    return model.create_feedback(state or model.empty_state(), **values)


class FeedbackTests(unittest.TestCase):
    def test_schema_exposes_all_required_reasons_and_targets(self):
        self.assertEqual(len(model.REASONS), 9)
        self.assertTrue({"EVENT", "ENTITY", "QUERY", "ANSWER"}.issubset(model.TARGET_TYPES))
        state, item, created = make_feedback()
        self.assertTrue(created)
        model.validate_state(state)
        self.assertEqual(item["review_status"], "NEW")

    def test_same_fingerprint_is_deduplicated(self):
        state, first, _ = make_feedback()
        state, second, created = make_feedback(state)
        self.assertFalse(created)
        self.assertEqual(first["feedback_id"], second["feedback_id"])
        self.assertEqual(len(state["items"]), 1)

    def test_review_acceptance_generates_manual_regression_link(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        record = accepted["items"][item["feedback_id"]]
        self.assertEqual(record["review_status"], "ACCEPTED")
        self.assertEqual(record["regression_fixture"]["reason"], "FALSE_MERGE")
        self.assertTrue(record["regression_fixture"]["requires_gold_promotion_review"])

    def test_rejected_feedback_does_not_create_regression_or_change_production(self):
        state, item, _ = make_feedback()
        rejected = model.review(state, item["feedback_id"], "REJECTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        record = rejected["items"][item["feedback_id"]]
        self.assertIsNone(record["regression_fixture"])
        self.assertNotIn("registry_update", record)
        self.assertNotIn("production_rule_update", record)

    def test_statistics_include_reason_status_and_trace_versions(self):
        state, _, _ = make_feedback()
        state, _, _ = make_feedback(
            state,
            target_type="ANSWER",
            target_id="answer-1",
            target_version="publication-2",
            reason="UNSUPPORTED_ANSWER",
            original_output_sha256="b" * 64,
            corrected_expected_state={"support_status": "UNSUPPORTED"},
        )
        summary = model.statistics(state)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_reason"]["FALSE_MERGE"], 1)
        self.assertEqual(summary["by_reason"]["UNSUPPORTED_ANSWER"], 1)
        self.assertEqual(summary["model_versions"], ["model:test"])
        self.assertEqual(summary["parser_versions"], ["parser:test"])

    def test_raw_private_content_is_rejected(self):
        state, item, _ = make_feedback()
        for field in ("conversation", "raw_prompt", "full_text", "private_notes"):
            broken = copy.deepcopy(state)
            broken["items"][item["feedback_id"]][field] = "private chat"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"must not contain {field}"):
                model.validate_state(broken)

    def test_non_object_state_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "state must be an object"):
            model.validate_state([])

    def test_invalid_target_and_unreviewed_regression_fail_closed(self):
        with self.assertRaises(ValueError):
            make_feedback(target_type="SOURCE")
        for field in ("target_type", "target_id", "target_version", "reason"):
            with self.assertRaises(ValueError):
                make_feedback(**{field: None})
        state, item, _ = make_feedback()
        with self.assertRaisesRegex(ValueError, "review status"):
            model.review(state, item["feedback_id"], None, reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        with self.assertRaisesRegex(ValueError, "reviewer_ref"):
            model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref=None, decided_at="2026-09-21T00:01:00+08:00")
        with self.assertRaisesRegex(ValueError, "accepted"):
            model.link_regression(state, item["feedback_id"], "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        with self.assertRaisesRegex(ValueError, "fixture_id"):
            model.link_regression(accepted, item["feedback_id"], None, reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")

    def test_tampered_feedback_identity_and_audit_fail_closed(self):
        state, item, _ = make_feedback()
        broken_identity = copy.deepcopy(state)
        broken_identity["items"][item["feedback_id"]]["fingerprint"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "feedback ID does not match fingerprint"):
            model.validate_state(broken_identity)
        # Rewriting the ID and its object key together keeps the state self
        # consistent, so only the derivation from the fingerprint can catch it.
        forged_key = "FEEDBACK-" + "C" * 20
        broken_derived = copy.deepcopy(state)
        broken_derived["items"][forged_key] = broken_derived["items"].pop(item["feedback_id"])
        broken_derived["items"][forged_key]["feedback_id"] = forged_key
        with self.assertRaisesRegex(ValueError, "feedback ID does not match fingerprint"):
            model.validate_state(broken_derived)
        mismatched_key = copy.deepcopy(state)
        mismatched_key["items"]["FEEDBACK-" + "D" * 20] = mismatched_key["items"][item["feedback_id"]]
        with self.assertRaisesRegex(ValueError, "stable object keys"):
            model.validate_state(mismatched_key)

        broken_audit = copy.deepcopy(state)
        broken_audit["items"][item["feedback_id"]]["audit"][0]["payload"]["reason"] = "WRONG_ENTITY"
        with self.assertRaisesRegex(ValueError, "audit binding"):
            model.validate_state(broken_audit)

        broken_owner = copy.deepcopy(state)
        broken_owner["items"][item["feedback_id"]]["audit"][0]["feedback_id"] = "FEEDBACK-" + "E" * 20
        with self.assertRaisesRegex(ValueError, "audit identity"):
            model.validate_state(broken_owner)

        broken_sequence = copy.deepcopy(state)
        broken_sequence["items"][item["feedback_id"]]["audit"][0]["sequence"] = 2
        with self.assertRaisesRegex(ValueError, "audit sequence"):
            model.validate_state(broken_sequence)

    def test_false_merge_and_wrong_entity_propose_registry_and_fusion_routes(self):
        for reason, output_hash in (("FALSE_MERGE", "0f" * 32), ("WRONG_ENTITY", "0e" * 32)):
            state, item, _ = make_feedback(reason=reason, original_output_sha256=output_hash)
            accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
            fixture = accepted["items"][item["feedback_id"]]["regression_fixture"]
            self.assertEqual(fixture["promotion_targets"], ["ENTITY_REGISTRY", "EVENT_FUSION"])
            self.assertEqual(fixture["effect_scope"], "TRUTH_CORRECTION")
            self.assertTrue(fixture["mutates_verified_truth"])
            self.assertTrue(fixture["requires_human_approval"])

    def test_unsupported_answer_routes_to_answer_gate_and_gold_dataset(self):
        state, item, _ = make_feedback(
            target_type="ANSWER",
            target_id="answer-1",
            target_version="publication-2",
            reason="UNSUPPORTED_ANSWER",
            original_output_sha256="c" * 64,
            corrected_expected_state={"support_status": "UNSUPPORTED"},
        )
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        fixture = accepted["items"][item["feedback_id"]]["regression_fixture"]
        self.assertEqual(fixture["promotion_targets"], ["ANSWER_EVIDENCE_GATE", "GOLD_DATASET"])
        self.assertTrue(fixture["requires_human_approval"])

    def test_not_relevant_feedback_stays_relevance_only_and_cannot_retract_truth(self):
        for corrected in (
            {"relevance": "NOT_RELEVANT"},
            {"relevance_score": 0},
            {"relevance": "NOT_RELEVANT", "relevance_note": "off-topic for my role"},
        ):
            state, item, _ = make_feedback(
                reason="NOT_RELEVANT",
                original_output_sha256="d" * 64,
                corrected_expected_state=corrected,
            )
            accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
            fixture = accepted["items"][item["feedback_id"]]["regression_fixture"]
            self.assertEqual(fixture["effect_scope"], "RELEVANCE_ONLY")
            self.assertFalse(fixture["mutates_verified_truth"])
            self.assertEqual(fixture["promotion_targets"], ["RELEVANCE_EVALUATION"])
            self.assertEqual(fixture["expected"], corrected)
        state, item, _ = make_feedback(
            reason="NOT_RELEVANT",
            original_output_sha256="d" * 64,
            corrected_expected_state={"relevance": "NOT_RELEVANT"},
        )
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        fixture = accepted["items"][item["feedback_id"]]["regression_fixture"]
        self.assertNotIn("retract", fixture["expected"])

    def test_relevance_only_corrected_state_allows_only_relevance_keys(self):
        for corrected in (
            {"delete_event": True},
            {"expected": {"delete_event": True}},
            {"relevance": {"retract": "PE-1"}},
            {"relevance": [{"verified": False}]},
            {None: "delete_event"},
            {"purge": True},
        ):
            with self.assertRaisesRegex(ValueError, "relevance-only"):
                make_feedback(
                    reason="NOT_RELEVANT",
                    original_output_sha256="d" * 64,
                    corrected_expected_state=corrected,
                )

    def test_relevance_only_violation_in_stored_state_fails_closed(self):
        state, item, _ = make_feedback(
            reason="NOT_RELEVANT",
            original_output_sha256="d" * 64,
            corrected_expected_state={"relevance": "NOT_RELEVANT"},
        )
        tampered = copy.deepcopy(state)
        tampered["items"][item["feedback_id"]]["corrected_expected_state"] = {"delete_event": True}
        with self.assertRaisesRegex(ValueError, "relevance-only"):
            model.validate_state(tampered)

    def test_regression_fixture_cannot_be_built_for_unreviewed_feedback(self):
        for status in ("NEW", "REJECTED", "DUPLICATE"):
            state, item, _ = make_feedback()
            candidate = copy.deepcopy(state)
            candidate["items"][item["feedback_id"]]["review_status"] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "only accepted feedback"):
                model.build_regression_fixture(candidate["items"][item["feedback_id"]], reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")

    def test_truth_correction_reasons_may_still_assert_retraction(self):
        state, item, created = make_feedback(
            reason="FALSE_MERGE",
            corrected_expected_state={"same_event": False, "remove_event": True},
        )
        self.assertTrue(created)

    def test_fixture_promotion_targets_are_bound_to_the_reviewed_reason(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        broken = copy.deepcopy(accepted)
        broken["items"][item["feedback_id"]]["regression_fixture"]["promotion_targets"] = ["SOURCE_POLICY"]
        with self.assertRaisesRegex(ValueError, "promotion_targets"):
            model.validate_state(broken)

    def test_fixture_payload_is_bound_to_the_reviewed_feedback(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        mutations = (
            ("expected", {"retract": "PE-1", "delete_event": True}),
            ("target", {"type": "EVENT", "id": "PE-other", "version": "v9"}),
            ("feedback_id", "FEEDBACK-00000000000000000000"),
            ("requires_gold_promotion_review", False),
            ("reason", "NOT_RELEVANT"),
            ("requires_human_approval", 1),
            ("mutates_verified_truth", 1),
            ("reviewer_ref", None),
            ("reviewer_ref", ""),
            ("reviewer_ref", 0),
            ("reviewer_ref", {"auto": "approve"}),
            ("reason", None),
        )
        for field, value in mutations:
            broken = copy.deepcopy(accepted)
            broken["items"][item["feedback_id"]]["regression_fixture"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                model.validate_state(broken)

    def test_only_accepted_feedback_may_carry_a_promotion_fixture(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        downgraded = copy.deepcopy(accepted)
        record = downgraded["items"][item["feedback_id"]]
        record["review_status"] = "REJECTED"
        record["decision"] = {"status": "REJECTED", "reviewer_ref": "reviewer:1", "decided_at": "2026-09-21T00:01:00+08:00"}
        with self.assertRaisesRegex(ValueError, "only accepted feedback"):
            model.validate_state(downgraded)

    def test_legacy_fixture_without_route_metadata_is_derived_on_load(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        legacy = copy.deepcopy(accepted)
        for field in model.ROUTE_FIELDS:
            del legacy["items"][item["feedback_id"]]["regression_fixture"][field]
        normalized = model.normalize_state(legacy)
        fixture = normalized["items"][item["feedback_id"]]["regression_fixture"]
        self.assertEqual(fixture["promotion_targets"], ["ENTITY_REGISTRY", "EVENT_FUSION"])
        self.assertEqual(fixture["effect_scope"], "TRUTH_CORRECTION")
        # The caller's object is left untouched, so a rejected candidate cannot
        # leave a half-upgraded state behind to be persisted by mistake.
        self.assertNotIn("promotion_targets", legacy["items"][item["feedback_id"]]["regression_fixture"])

        partial = copy.deepcopy(accepted)
        partial_fixture = partial["items"][item["feedback_id"]]["regression_fixture"]
        del partial_fixture["effect_scope"]
        del partial_fixture["requires_human_approval"]
        for candidate in (partial, copy.deepcopy(partial)):
            with self.assertRaisesRegex(ValueError, "route fields are incomplete"):
                model.normalize_state(candidate)
        # A record the normaliser cannot derive for is left to the validator, so the
        # operator sees the reason/status error rather than a route-derivation one.
        unbackfilled = copy.deepcopy(accepted)
        for field in model.ROUTE_FIELDS:
            del unbackfilled["items"][item["feedback_id"]]["regression_fixture"][field]
        unknown_reason = copy.deepcopy(unbackfilled)
        unknown_reason["items"][item["feedback_id"]]["reason"] = "BOGUS_REASON"
        with self.assertRaisesRegex(ValueError, "reason/status is invalid"):
            model.normalize_state(unknown_reason)
        with self.assertRaisesRegex(ValueError, "missing route metadata"):
            model.validate_state(copy.deepcopy(unbackfilled))

    def test_fixture_kind_cannot_be_relabelled_to_skip_the_expected_binding(self):
        state, item, _ = make_feedback(
            reason="NOT_RELEVANT",
            original_output_sha256="d" * 64,
            corrected_expected_state={"relevance": "NOT_RELEVANT"},
        )
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        # An unknown kind is rejected by the enum alone, with no other field wrong.
        for kind in ("FEEDBACK_REGRESSION_V2", "", None, 1, "linked_regression"):
            broken = copy.deepcopy(accepted)
            broken["items"][item["feedback_id"]]["regression_fixture"]["fixture_kind"] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "fixture_kind"):
                model.normalize_state(broken)
        # Relabelling as a linked fixture must not let `expected`/`target` through.
        broken = copy.deepcopy(accepted)
        fixture = broken["items"][item["feedback_id"]]["regression_fixture"]
        fixture["fixture_kind"] = "LINKED_REGRESSION"
        fixture["fixture_id"] = "GOLD-1"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            model.normalize_state(broken)

    def test_every_feedback_timestamp_must_carry_a_timezone(self):
        state, item, _ = make_feedback()
        naive = "2026-09-21T00:00:00"
        for field in ("created_at", "updated_at"):
            broken = copy.deepcopy(state)
            broken["items"][item["feedback_id"]][field] = naive
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "must include timezone"):
                model.normalize_state(broken)
        broken = copy.deepcopy(state)
        broken["items"][item["feedback_id"]]["audit"][0]["at"] = naive
        with self.assertRaisesRegex(ValueError, "must include timezone"):
            model.normalize_state(broken)
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        for site, candidate in (
            ("decision.decided_at", copy.deepcopy(accepted)),
            ("regression_fixture.decided_at", copy.deepcopy(accepted)),
            ("last_updated_at", copy.deepcopy(accepted)),
        ):
            cursor = candidate["items"][item["feedback_id"]]
            cursor = cursor["decision"] if site.startswith("decision") else cursor
            cursor = cursor["regression_fixture"] if site.startswith("regression") else cursor
            cursor = candidate if site == "last_updated_at" else cursor
            cursor[site.split(".")[-1]] = naive
            with self.subTest(site=site), self.assertRaisesRegex(ValueError, "must include timezone"):
                model.normalize_state(candidate)

    def test_review_status_gate_and_enum_are_enforced(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        rejected = model.review(state, item["feedback_id"], "REJECTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        for source in (accepted, rejected):
            for status in ("ACCEPTED", "REJECTED", "DUPLICATE"):
                with self.subTest(source_status=source["items"][item["feedback_id"]]["review_status"], status=status), self.assertRaisesRegex(ValueError, "already reviewed"):
                    model.review(source, item["feedback_id"], status, reviewer_ref="mallory", decided_at="2026-09-21T00:02:00+08:00")
        for status in ("PENDING", "", "ACCEPTED "):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "must be ACCEPTED, REJECTED, or DUPLICATE"):
                model.review(state, item["feedback_id"], status, reviewer_ref="reviewer:1", decided_at="2026-09-21T00:02:00+08:00")

    def test_linked_regression_cannot_smuggle_a_claim_about_the_record(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        linked = model.link_regression(accepted, item["feedback_id"], "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")
        model.normalize_state(linked)
        for field, value in (("expected", {"delete_event": True}), ("target", {"type": "EVENT", "id": "PE-x", "version": "v9"}), ("auto_apply", True)):
            broken = copy.deepcopy(linked)
            broken["items"][item["feedback_id"]]["regression_fixture"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported fields"):
                model.normalize_state(broken)

    def test_fixture_kinds_keep_their_own_timestamp_marker(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        linked = model.link_regression(accepted, item["feedback_id"], "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")
        model.normalize_state(linked)
        relabelled = copy.deepcopy(accepted)
        fixture = relabelled["items"][item["feedback_id"]]["regression_fixture"]
        fixture["fixture_kind"] = "LINKED_REGRESSION"
        fixture["fixture_id"] = "GOLD-1"
        del fixture["target"]
        del fixture["expected"]
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            model.normalize_state(relabelled)
        for candidate, marker in ((linked, "linked_at"), (accepted, "decided_at")):
            without_marker = copy.deepcopy(candidate)
            del without_marker["items"][item["feedback_id"]]["regression_fixture"][marker]
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, f"missing required fields: {marker}"):
                model.normalize_state(without_marker)

    def test_record_schema_is_closed_so_no_auto_apply_flag_can_hide(self):
        state, item, _ = make_feedback()
        for field, value in (
            ("auto_apply", True),
            ("promote_to_registry", True),
            ("training_data", {"use_for_finetuning": True}),
            ("auto_promote", {"target": "ENTITY_REGISTRY"}),
        ):
            broken = copy.deepcopy(state)
            broken["items"][item["feedback_id"]][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unsupported fields"):
                model.normalize_state(broken)
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        for path, value in (
            (("target", "extra"), "x"),
            (("trace", "auto_apply"), True),
            (("decision", "auto_apply"), True),
        ):
            broken = copy.deepcopy(accepted)
            cursor = broken["items"][item["feedback_id"]]
            for key in path[:-1]:
                cursor = cursor[key]
            cursor[path[-1]] = value
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "unsupported fields"):
                model.normalize_state(broken)
        # The audit payload stays an open, hash-bound bag: any edit breaks audit_id.
        broken_audit = copy.deepcopy(accepted)
        broken_audit["items"][item["feedback_id"]]["audit"][0]["payload"]["auto_apply"] = True
        with self.assertRaisesRegex(ValueError, "audit binding"):
            model.normalize_state(broken_audit)

    def test_linked_review_id_cannot_smuggle_a_truth_assertion(self):
        for value in ({"retract": "PE-verified", "delete_event": True}, ["retract"], "", 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "linked_review_id"):
                make_feedback(
                    reason="NOT_RELEVANT",
                    original_output_sha256="d" * 64,
                    corrected_expected_state={"relevance": "NOT_RELEVANT"},
                    linked_review_id=value,
                )
        state, item, created = make_feedback(
            reason="NOT_RELEVANT",
            original_output_sha256="d" * 64,
            corrected_expected_state={"relevance": "NOT_RELEVANT"},
            linked_review_id="REVIEW-1",
        )
        self.assertTrue(created)
        tampered = copy.deepcopy(state)
        tampered["items"][item["feedback_id"]]["linked_review_id"] = "REVIEW-2"
        with self.assertRaisesRegex(ValueError, "fingerprint does not match"):
            model.normalize_state(tampered)
        smuggled = copy.deepcopy(state)
        smuggled["items"][item["feedback_id"]]["linked_review_id"] = {"retract": "PE-verified"}
        with self.assertRaisesRegex(ValueError, "linked_review_id"):
            model.normalize_state(smuggled)

    def test_unhashable_json_values_fail_closed_with_value_error(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        for field, value, message in (
            ("target", {"type": [], "id": "PE-1", "version": "v1"}, "feedback target type is invalid"),
            ("target", {"type": "SOURCE", "id": "PE-1", "version": "v1"}, "feedback target type is invalid"),
            ("reason", [], "feedback reason/status is invalid"),
            ("review_status", {}, "feedback reason/status is invalid"),
        ):
            broken = copy.deepcopy(state)
            broken["items"][item["feedback_id"]][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                model.normalize_state(broken)
        for kind in ([], {}, 1):
            broken = copy.deepcopy(accepted)
            broken["items"][item["feedback_id"]]["regression_fixture"]["fixture_kind"] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "fixture_kind is invalid"):
                model.normalize_state(broken)
        linked = model.link_regression(accepted, item["feedback_id"], "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")
        for source, marker in ((accepted, "decided_at"), (linked, "linked_at")):
            broken = copy.deepcopy(source)
            broken["items"][item["feedback_id"]]["regression_fixture"][marker] = "not-a-timestamp"
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "ISO-8601"):
                model.normalize_state(broken)
        stored_list = copy.deepcopy(accepted)
        stored_list["items"][item["feedback_id"]]["corrected_expected_state"] = ["same_event"]
        with self.assertRaisesRegex(ValueError, "corrected_expected_state must be an object"):
            model.normalize_state(stored_list)
        self.assertEqual(
            model.review(state, item["feedback_id"], "accepted", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:02:00+08:00")["items"][item["feedback_id"]]["review_status"],
            "ACCEPTED",
        )
        for source in (accepted, linked):
            for identifier in (None, "", "   ", 7, {"auto_apply": True}):
                broken = copy.deepcopy(source)
                broken["items"][item["feedback_id"]]["regression_fixture"]["fixture_id"] = identifier
                with self.subTest(kind=broken["items"][item["feedback_id"]]["regression_fixture"]["fixture_kind"], identifier=identifier), self.assertRaisesRegex(ValueError, "non-empty fixture_id"):
                    model.normalize_state(broken)

    def test_audit_entry_schema_is_closed(self):
        state, item, _ = make_feedback()
        broken = copy.deepcopy(state)
        broken["items"][item["feedback_id"]]["audit"][0]["auto_apply"] = True
        with self.assertRaisesRegex(ValueError, "audit entry has unsupported fields"):
            model.normalize_state(broken)

    def test_relevance_only_rejects_non_object_corrected_state(self):
        for corrected in ([{"delete_event": True}], ["relevance"], "relevance", 1):
            with self.subTest(corrected=corrected), self.assertRaisesRegex(ValueError, "corrected_expected_state must be an object"):
                make_feedback(
                    reason="NOT_RELEVANT",
                    original_output_sha256="d" * 64,
                    corrected_expected_state=corrected,
                )

    def test_truth_correction_reasons_also_reject_non_object_corrected_state(self):
        for corrected in ([], ["same_event"], "same_event", 0):
            with self.subTest(corrected=corrected), self.assertRaisesRegex(ValueError, "corrected_expected_state must be an object"):
                make_feedback(corrected_expected_state=corrected)

    def test_relevance_only_evidence_refs_must_be_reference_strings(self):
        for refs in ([{"delete_event": True}], [""], ["  "], [7]):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                make_feedback(
                    reason="NOT_RELEVANT",
                    original_output_sha256="d" * 64,
                    corrected_expected_state={"relevance": "NOT_RELEVANT"},
                    evidence_refs=refs,
                )
        # Truth-correction reasons may still carry structured evidence references.
        state, _, created = make_feedback(evidence_refs=[{"locator": "S-036#fixture"}])
        self.assertTrue(created)
        for refs in (5, 3.5, object(), "S-036"):
            with self.subTest(refs=refs), self.assertRaisesRegex(ValueError, "evidence_refs must be a string/object array"):
                make_feedback(reason="NOT_RELEVANT", evidence_refs=refs)

    def test_state_envelope_is_closed_and_schema_version_is_strict(self):
        state, _, _ = make_feedback()
        for extra in ({"auto_apply": True}, {"promotion_targets": ["SOURCE_POLICY"]}, {"auto_promote": 1}):
            broken = copy.deepcopy(state)
            broken.update(extra)
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "state has unsupported fields"):
                model.normalize_state(broken)
        for version in (True, 1.0, "1", 2):
            broken = copy.deepcopy(state)
            broken["schema_version"] = version
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "schema_version=1"):
                model.normalize_state(broken)

    def test_review_decision_must_match_the_review_status(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        for decision in (
            {"status": "REJECTED", "reviewer_ref": "reviewer:1", "decided_at": "2026-09-21T00:01:00+08:00"},
            {"status": "ACCEPTED", "reviewer_ref": "", "decided_at": "2026-09-21T00:01:00+08:00"},
            {"status": "ACCEPTED", "reviewer_ref": None, "decided_at": "2026-09-21T00:01:00+08:00"},
            {"status": "ACCEPTED", "reviewer_ref": 7, "decided_at": "2026-09-21T00:01:00+08:00"},
        ):
            broken = copy.deepcopy(accepted)
            broken["items"][item["feedback_id"]]["decision"] = decision
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, "bound decision"):
                model.normalize_state(broken)
        # An unnamed approver must not be able to propose a promotion.
        broken_fixture = copy.deepcopy(accepted)
        broken_fixture["items"][item["feedback_id"]]["regression_fixture"]["reviewer_ref"] = "intern-b"
        with self.assertRaisesRegex(ValueError, "must match the approving reviewer"):
            model.normalize_state(broken_fixture)

    def test_new_feedback_cannot_carry_a_review_decision(self):
        state, item, _ = make_feedback()
        for decision in ({"status": "ACCEPTED", "reviewer_ref": "x", "decided_at": "2026-09-21T00:01:00+08:00"}, {}, 1):
            broken = copy.deepcopy(state)
            broken["items"][item["feedback_id"]]["decision"] = decision
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, "new feedback cannot contain a review decision"):
                model.normalize_state(broken)

    def test_review_and_link_reject_a_non_string_feedback_id(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        for identifier in ([], {}, 7, "", "  "):
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(ValueError, "feedback_id is required"):
                    model.review(accepted, identifier, "REJECTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
                with self.assertRaisesRegex(ValueError, "feedback_id is required"):
                    model.link_regression(accepted, identifier, "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")

    def test_rewritten_record_content_is_caught_by_the_fingerprint(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        tampered = copy.deepcopy(accepted)
        record = tampered["items"][item["feedback_id"]]
        record["corrected_expected_state"] = {"delete_event": True}
        record["regression_fixture"]["expected"] = {"delete_event": True}
        with self.assertRaisesRegex(ValueError, "fingerprint does not match"):
            model.validate_state(tampered)

    def test_generated_fixture_id_is_derived_from_the_feedback_id(self):
        state, item, _ = make_feedback()
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        self.assertEqual(
            accepted["items"][item["feedback_id"]]["regression_fixture"]["fixture_id"],
            f"FEEDBACK-REGRESSION-{item['feedback_id'].removeprefix('FEEDBACK-')}",
        )
        broken = copy.deepcopy(accepted)
        broken["items"][item["feedback_id"]]["regression_fixture"]["fixture_id"] = "GOLD-1"
        with self.assertRaisesRegex(ValueError, "fixture_id must be derived"):
            model.normalize_state(broken)

    def test_relevance_only_rejects_containers_json_cannot_round_trip(self):
        for corrected in ({"relevance": ({"delete_event": True},)}, {"relevance": {"tags", "other"}}):
            with self.assertRaisesRegex(ValueError, "JSON-compatible"):
                make_feedback(
                    reason="NOT_RELEVANT",
                    original_output_sha256="d" * 64,
                    corrected_expected_state=corrected,
                )

    def test_linked_regression_keeps_route_effect_scope_and_human_approval(self):
        state, item, _ = make_feedback(
            reason="MISSING_EVENT",
            original_output_sha256="e" * 64,
            corrected_expected_state={"missing": True},
        )
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        stranger = model.link_regression(accepted, item["feedback_id"], "gold-1", reviewer_ref="total-stranger", linked_at="2026-09-21T00:01:00+08:00")
        with self.assertRaisesRegex(ValueError, "must match the approving reviewer"):
            model.normalize_state(stranger)
        linked = model.link_regression(accepted, item["feedback_id"], "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")
        for reviewer in (None, "", 0, []):
            broken = copy.deepcopy(linked)
            broken["items"][item["feedback_id"]]["regression_fixture"]["reviewer_ref"] = reviewer
            with self.subTest(reviewer=reviewer), self.assertRaisesRegex(ValueError, "must name the approving human"):
                model.normalize_state(broken)
        fixture = linked["items"][item["feedback_id"]]["regression_fixture"]
        self.assertEqual(fixture["fixture_id"], "gold-1")
        self.assertEqual(fixture["promotion_targets"], ["EVENT_FUSION", "GOLD_DATASET"])
        self.assertEqual(fixture["effect_scope"], "TRUTH_CORRECTION")
        self.assertTrue(fixture["requires_human_approval"])
        model.validate_state(linked)

    def test_route_fields_cover_every_reason_and_declared_contract(self):
        expected_routes = {
            "FALSE_MERGE": ("ENTITY_REGISTRY", "EVENT_FUSION"),
            "MISSED_MERGE": ("ENTITY_REGISTRY", "EVENT_FUSION"),
            "WRONG_ENTITY": ("ENTITY_REGISTRY", "EVENT_FUSION"),
            "NOT_RELEVANT": ("RELEVANCE_EVALUATION",),
            "MISSING_EVENT": ("EVENT_FUSION", "GOLD_DATASET"),
            "WRONG_CHANGE_CLASSIFICATION": ("EVENT_FUSION", "GOLD_DATASET"),
            "UNSUPPORTED_ANSWER": ("ANSWER_EVIDENCE_GATE", "GOLD_DATASET"),
            "WRONG_STATISTIC_SCOPE": ("ANSWER_EVIDENCE_GATE", "GOLD_DATASET"),
            "BAD_SOURCE_MAPPING": ("EVENT_FUSION", "SOURCE_POLICY"),
        }
        self.assertEqual(model.PROMOTION_ROUTES, expected_routes)
        self.assertEqual(set(model.PROMOTION_TARGETS), {"ANSWER_EVIDENCE_GATE", "ENTITY_REGISTRY", "EVENT_FUSION", "GOLD_DATASET", "RELEVANCE_EVALUATION", "SOURCE_POLICY"})
        self.assertEqual({target for route in expected_routes.values() for target in route}, set(model.PROMOTION_TARGETS))
        for reason in sorted(expected_routes):
            with self.subTest(reason=reason):
                self.assertIn(model.effect_scope_for(reason), model.EFFECT_SCOPES)
                self.assertEqual(model.route_fields_for(reason)["promotion_targets"], list(expected_routes[reason]))
                state, item, created = make_feedback(
                    reason=reason,
                    original_output_sha256="9" * 64,
                    corrected_expected_state=(
                        {"relevance": "NOT_RELEVANT"} if reason == "NOT_RELEVANT" else {"same_event": False}
                    ),
                )
                self.assertTrue(created)
                accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
                fixture = accepted["items"][item["feedback_id"]]["regression_fixture"]
                self.assertEqual(fixture["reason"], reason)
                self.assertEqual(set(model.FIXTURE_KEYS["FEEDBACK_REGRESSION"]), set(fixture) - set(model.ROUTE_FIELDS))
                model.normalize_state(accepted)

    def test_statistics_report_promotion_routes_and_effect_scopes(self):
        state, merge_item, _ = make_feedback()
        state, irrelevant_item, _ = make_feedback(
            state,
            reason="NOT_RELEVANT",
            original_output_sha256="d" * 64,
            corrected_expected_state={"relevance": "NOT_RELEVANT"},
        )
        state, rejected_item, _ = make_feedback(
            state,
            reason="MISSED_MERGE",
            original_output_sha256="f" * 64,
            corrected_expected_state={"same_event": True},
        )
        for identifier in (merge_item, irrelevant_item, rejected_item):
            status = "REJECTED" if identifier is rejected_item else "ACCEPTED"
            state = model.review(state, identifier["feedback_id"], status, reviewer_ref="reviewer:1", decided_at="2026-09-21T00:01:00+08:00")
        summary = model.statistics(state)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["accepted"], 2)
        self.assertEqual(summary["by_reason"]["FALSE_MERGE"], 1)
        self.assertEqual(summary["by_reason"]["MISSED_MERGE"], 1)
        self.assertEqual(summary["by_status"]["REJECTED"], 1)
        self.assertEqual(summary["by_route"]["ENTITY_REGISTRY"], 1)
        self.assertEqual(summary["by_route"]["EVENT_FUSION"], 1)
        self.assertEqual(summary["by_route"]["RELEVANCE_EVALUATION"], 1)
        self.assertEqual(summary["by_route"]["GOLD_DATASET"], 0)
        self.assertEqual(summary["by_effect_scope"], {"RELEVANCE_ONLY": 1, "TRUTH_CORRECTION": 1})
        self.assertEqual(sum(summary["by_route"].values()), 3)


class FeedbackPrivateFieldBoundaryTests(unittest.TestCase):
    PRIVATE_FIELDS = ("raw_prompt", "conversation", "full_text", "private_notes")

    @staticmethod
    def payload(field):
        return {"locator": "fixture:owned-local", "details": [{"nested": {field: "SYNTHETIC private content"}}]}

    @staticmethod
    def stored_private_record(container, payload):
        state, item, _ = make_feedback(corrected_expected_state={}, evidence_refs=[])
        item[container] = payload
        fingerprint = model.fingerprint_for(item["target"], item["reason"], item["original_output_sha256"],
                                            item["corrected_expected_state"], item["evidence_refs"], item["linked_review_id"])
        item["fingerprint"] = fingerprint
        item["feedback_id"] = model.feedback_id_for(fingerprint)
        item["audit"] = []
        item["audit"] = [model._audit(item, "CREATED", item["created_at"], {"reason": item["reason"], "target": item["target"]})]
        state["items"] = {item["feedback_id"]: item}
        return state

    def test_creation_refuses_known_private_keys_in_nested_corrections_and_locators(self):
        for field in self.PRIVATE_FIELDS:
            for spelling in (field, field.upper()):
                for container in ("corrected_expected_state", "evidence_refs"):
                    with self.subTest(field=spelling, container=container):
                        payload = self.payload(spelling)
                        value = [payload] if container == "evidence_refs" else payload
                        original = copy.deepcopy(value)
                        state = model.empty_state()
                        with self.assertRaisesRegex(ValueError, "private field") as caught:
                            make_feedback(state, **{container: value})
                        self.assertNotIn("SYNTHETIC private content", str(caught.exception))
                        self.assertEqual(value, original)
                        self.assertEqual(state, model.empty_state())

    def test_self_consistent_stored_private_records_are_refused_before_review_or_rewrite(self):
        for field in self.PRIVATE_FIELDS:
            for container in ("corrected_expected_state", "evidence_refs"):
                with self.subTest(field=field, container=container):
                    payload = self.payload(field)
                    state = self.stored_private_record(container, [payload] if container == "evidence_refs" else payload)
                    before = copy.deepcopy(state)
                    for load in (model.validate_state, model.normalize_state):
                        with self.assertRaisesRegex(ValueError, "private field"):
                            load(state)
                    identifier = next(iter(state["items"]))
                    with self.assertRaisesRegex(ValueError, "private field"):
                        model.review(state, identifier, "ACCEPTED", reviewer_ref="synthetic-reviewer", decided_at="2026-10-08T12:00:00+00:00")
                    self.assertEqual(state, before)

    def test_refused_cli_add_keeps_existing_state_and_does_not_create_missing_output(self):
        for field in self.PRIVATE_FIELDS:
            for container in ("corrected_expected_state", "evidence_refs"):
                for existing in (False, True):
                    with self.subTest(field=field, container=container, existing=existing):
                        with tempfile.TemporaryDirectory() as directory:
                            state_path = Path(directory) / "state.json"
                            if existing:
                                cli.write_state(state_path, model.empty_state())
                            before = state_path.read_bytes() if existing else None
                            payload = self.payload(field)
                            option, value = ("--evidence-refs", [payload]) if container == "evidence_refs" else ("--corrected-state", payload)
                            args = ["--state", str(state_path), "add", "--target-type", "ANSWER", "--target-id", "SYNTHETIC-PRIVATE",
                                    "--target-version", "synthetic-v1", "--reason", "UNSUPPORTED_ANSWER", "--output-hash", "a" * 64,
                                    option, json.dumps(value), "--at", "2026-10-08T12:00:00+00:00"]
                            parsed = cli.parser().parse_args(args)
                            with self.assertRaisesRegex(ValueError, "private field"):
                                parsed.handler(parsed)
                            self.assertEqual(state_path.read_bytes() if state_path.exists() else None, before)
                            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["state.json"] if existing else [])

    def test_refused_cli_load_does_not_overwrite_self_consistent_legacy_state(self):
        for container in ("corrected_expected_state", "evidence_refs"):
            with self.subTest(container=container), tempfile.TemporaryDirectory() as directory:
                payload = self.payload("raw_prompt")
                state = self.stored_private_record(container, [payload] if container == "evidence_refs" else payload)
                state_path = Path(directory) / "legacy.json"
                state_path.write_text(json.dumps(state), encoding="utf-8")
                before = state_path.read_bytes()
                identifier = next(iter(state["items"]))
                args = cli.parser().parse_args(["--state", str(state_path), "review", "--feedback-id", identifier,
                                              "--status", "ACCEPTED", "--reviewer-ref", "synthetic-reviewer"])
                with self.assertRaisesRegex(ValueError, "private field"):
                    args.handler(args)
                self.assertEqual(state_path.read_bytes(), before)
                self.assertEqual([p.name for p in Path(directory).iterdir()], ["legacy.json"])

    def test_ordinary_expected_states_minimal_locators_and_necessary_snippets_remain_allowed(self):
        corrected = {"same_event": False, "expected": [{"entity_id": "synthetic-entity", "excerpt": "ordinary necessary correction snippet"}]}
        refs = ["fixture:owned-local", {"locator": "fixture:section-1", "ids": ["synthetic-evidence"]}]
        state, item, _ = make_feedback(corrected_expected_state=corrected, evidence_refs=refs)
        model.validate_state(state)
        self.assertEqual(item["corrected_expected_state"], corrected)
        self.assertEqual(item["evidence_refs"], refs)
        accepted = model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="synthetic-reviewer", decided_at="2026-10-08T12:00:00+00:00")
        model.validate_state(accepted)
        self.assertEqual(accepted["items"][item["feedback_id"]]["regression_fixture"]["expected"], corrected)

    def test_json_serializable_tuple_does_not_bypass_nested_private_field_check(self):
        with self.assertRaisesRegex(ValueError, "private field"):
            make_feedback(corrected_expected_state={"expected": ({"raw_prompt": "SYNTHETIC private content"},)})

    def test_self_consistent_nested_audit_payload_cannot_retain_private_fields(self):
        for field in self.PRIVATE_FIELDS:
            for spelling in (field, field.upper()):
                with self.subTest(field=spelling):
                    state, item, _ = make_feedback()
                    entry = item["audit"][0]
                    entry["payload"]["details"] = [{spelling: "SYNTHETIC private content"}]
                    entry["audit_id"] = model._audit_id(entry)
                    before = copy.deepcopy(state)
                    with self.assertRaisesRegex(ValueError, "private field"):
                        model.normalize_state(state)
                    with self.assertRaisesRegex(ValueError, "private field"):
                        model.review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="synthetic-reviewer", decided_at="2026-10-08T12:00:00+00:00")
                    self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
