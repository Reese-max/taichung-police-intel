import copy
import importlib.util
from pathlib import Path
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
        broken = copy.deepcopy(state)
        broken["items"][item["feedback_id"]]["conversation"] = "private chat"
        with self.assertRaisesRegex(ValueError, "conversation"):
            model.validate_state(broken)

    def test_invalid_target_and_unreviewed_regression_fail_closed(self):
        with self.assertRaises(ValueError):
            make_feedback(target_type="SOURCE")
        state, item, _ = make_feedback()
        with self.assertRaisesRegex(ValueError, "accepted"):
            model.link_regression(state, item["feedback_id"], "gold-1", reviewer_ref="reviewer:1", linked_at="2026-09-21T00:01:00+08:00")


if __name__ == "__main__":
    unittest.main()
