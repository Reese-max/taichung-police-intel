from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from source_value_contract import item_score, route_item, source_score, transition


HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "source-value.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
HIGH_COMPONENTS = {
    "mission_fit": 30,
    "actionable_change": 20,
    "local_police_relevance": 20,
    "evidence_strength": 15,
    "novelty_corroboration": 10,
}
ZERO_COMPONENTS = {name: 0 for name in HIGH_COMPONENTS}


def payload(decision: dict, **overrides) -> dict:
    value = {
        "contract_version": "1.0",
        "record_type": "item_value_decision",
        "source_id": "S-029",
        "raw_item_id": "1150803-2",
        "traceability_gate": "PASS",
        "verification_status": decision["verification_status"],
        "content_disposition": decision["content_disposition"],
        "item_value_score": decision["item_value_score"],
        "score_components": HIGH_COMPONENTS,
        "score_reason_codes": decision["score_reason_codes"],
        "dedup_cluster_id": None,
        "duplicate_of": None,
        "new_information_fields": ["new_evidence"],
        "validation_reason_codes": decision["validation_reason_codes"],
        "producer_run_id": "producer-001",
        "verifier_run_id": "verifier-001",
        "producer_model": "model-a",
        "verifier_model": "model-b",
        "prompt_version": "source-value-v1",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "original_url": "https://www.rdec.taichung.gov.tw/media/example.pdf",
        "publisher": "臺中市政府研究發展考核委員會",
        "content_sha256": "a" * 64,
        "evidence_locators": ["pdf:1"],
    }
    value.update(overrides)
    return value


class SourceValueContractTests(unittest.TestCase):
    def test_schema_is_valid_draft_2020_12(self):
        Draft202012Validator.check_schema(SCHEMA)

    def test_high_value_item_enters_home_candidate(self):
        decision = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            score_components=HIGH_COMPONENTS,
        )
        self.assertEqual(decision["item_value_score"], 95)
        self.assertEqual(decision["content_disposition"], "HOME_CANDIDATE")
        VALIDATOR.validate(payload(decision))

    def test_untraceable_item_is_quarantined(self):
        decision = route_item(verification_status="AUTO_PASS", traceability_gate="FAIL")
        rejected = payload(
            decision,
            traceability_gate="FAIL",
            score_components=ZERO_COMPONENTS,
            original_url=None,
            publisher=None,
            content_sha256=None,
            evidence_locators=[],
            producer_run_id=None,
            verifier_run_id=None,
            producer_model=None,
            verifier_model=None,
            prompt_version=None,
        )
        self.assertEqual(decision["validation_reason_codes"], ["UNTRACEABLE"])
        VALIDATOR.validate(rejected)

    def test_home_candidate_without_evidence_is_rejected_by_schema(self):
        decision = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            score_components=HIGH_COMPONENTS,
        )
        with self.assertRaises(ValidationError):
            VALIDATOR.validate(payload(decision, evidence_locators=[]))

    def test_exact_and_semantic_duplicates_are_suppressed(self):
        exact = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            exact_duplicate=True,
        )
        semantic = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            score_components=HIGH_COMPONENTS,
            semantic_duplicate=True,
        )
        self.assertEqual(exact["content_disposition"], "EXACT_DUPLICATE_SUPPRESSED")
        self.assertEqual(semantic["content_disposition"], "SEMANTIC_DUPLICATE_SUPPRESSED")

    def test_semantic_match_with_new_evidence_is_not_suppressed(self):
        decision = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            score_components=HIGH_COMPONENTS,
            semantic_duplicate=True,
            new_information_fields=["new_evidence"],
        )
        self.assertEqual(decision["content_disposition"], "HOME_CANDIDATE")
        self.assertIn("SEMANTIC_MATCH_WITH_NEW_INFORMATION", decision["score_reason_codes"])

    def test_role_caps_prevent_weak_sources_from_entering_home(self):
        context = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            score_components=HIGH_COMPONENTS,
            product_role="CONTEXT_ONLY",
        )
        discovery = route_item(
            verification_status="AUTO_PASS",
            traceability_gate="PASS",
            score_components=HIGH_COMPONENTS,
            product_role="DISCOVERY_ONLY",
            has_t1_t2_evidence=False,
        )
        self.assertEqual((context["item_value_score"], context["content_disposition"]), (49, "SEARCH_ONLY"))
        self.assertEqual((discovery["item_value_score"], discovery["content_disposition"]), (39, "LOW_VALUE_SUPPRESSED"))

    def test_score_component_boundary(self):
        self.assertEqual(item_score(HIGH_COMPONENTS), 95)
        with self.assertRaises(ValueError):
            item_score({**HIGH_COMPONENTS, "mission_fit": 31})

    def test_state_machine_allows_one_retry_then_quarantine(self):
        status = transition(None, "VALIDATION_DISAGREED")
        self.assertEqual(status, "AI_DISAGREEMENT")
        status = transition(status, "RETRY_SCHEDULED")
        self.assertEqual(status, "AUTO_RETRY")
        self.assertEqual(transition(status, "VALIDATION_DISAGREED"), "QUARANTINED")
        with self.assertRaises(ValueError):
            transition("AUTO_PASS", "RETRY_SCHEDULED")

    def test_source_score_and_prep_core_guard(self):
        low_rates = {name: 0.2 for name in (
            "useful_yield_rate",
            "traceable_rate",
            "unique_information_rate",
            "successful_window_rate",
        )}
        ordinary = source_score(low_rates, product_role="TREND_SIGNAL", consecutive_low_windows=2)
        prep_core = source_score(low_rates, product_role="PREP_CORE", consecutive_low_windows=2)
        self.assertEqual(ordinary["source_value_status"], "DEFERRED_BY_VALUE")
        self.assertEqual(prep_core["source_value_status"], "LOW_FREQUENCY")
        no_data = source_score(None, product_role="PREP_CORE")
        self.assertEqual(no_data["source_value_status"], "NOT_ENOUGH_DATA")


if __name__ == "__main__":
    unittest.main()
