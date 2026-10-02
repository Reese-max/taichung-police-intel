from __future__ import annotations

import copy
from pathlib import Path
import unittest

from intel_v2.located_facts import acquire_document, build_bundle, confirm_facts, extract_json_facts, validate_document_url, verify_fact


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "tests/fixtures/located-facts/official-news.html"
JSON = ROOT / "tests/fixtures/located-facts/official-data.json"
STAMP = "2026-09-21T00:00:00+00:00"


class LocatedFactsTests(unittest.TestCase):
    def html_document(self, body: bytes):
        return acquire_document(
            source_id="S-001",
            requested_url="https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
            final_url="https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
            body=body,
            content_type="text/html; charset=utf-8",
            fetched_at=STAMP,
        )

    def test_html_facts_keep_distinct_time_roles_and_verify_locator(self):
        body = HTML.read_bytes()
        document = self.html_document(body)
        bundle = build_bundle(document, body, [
            {"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00", "date_needle": "2026-09-21"},
            {"subject_id": "road:A", "predicate": "road_control_start_at", "needle": "16:00", "date_needle": "2026-09-21"},
        ])
        self.assertEqual({fact["predicate"] for fact in bundle["facts"]}, {"event_start_at", "road_control_start_at"})
        self.assertEqual({fact["valid_time"] for fact in bundle["facts"]}, {"2026-09-21"})
        self.assertEqual(bundle["receipt"]["needs_review_count"], 0)
        self.assertTrue(all(verify_fact(document, body, fact)["status"] == "PASS" for fact in bundle["facts"]))
        self.assertEqual(bundle["facts"][0]["verification_status"], "FACT_CANDIDATE")

    def test_json_pointer_normalizes_value_and_binds_hash(self):
        body = JSON.read_bytes()
        document = acquire_document(
            source_id="S-028",
            requested_url="https://data.gov.tw/dataset/88147",
            final_url="https://data.gov.tw/dataset/88147",
            body=body,
            content_type="application/json",
            fetched_at=STAMP,
        )
        facts = extract_json_facts(document, body, [{"subject_id": "crime:A", "predicate": "count", "pointer": "/event/count", "normalizer": "integer"}])
        self.assertEqual(facts[0]["raw_value"], "1,234")
        self.assertEqual(facts[0]["normalized_value"], 1234)
        self.assertEqual(verify_fact(document, body, facts[0])["status"], "PASS")

    def test_ambiguous_text_stays_needs_review_and_missing_date_is_null(self):
        body = "<p>時間 18:00</p><p>時間 18:00</p>".encode("utf-8")
        document = self.html_document(body)
        bundle = build_bundle(document, body, [{"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00"}])
        self.assertEqual(bundle["facts"][0]["verification_status"], "NEEDS_REVIEW")
        self.assertIsNone(bundle["facts"][0]["valid_time"])

    def test_invalid_calendar_date_stays_needs_review(self):
        body = "<p>公告日期 2026-02-31，活動 18:00</p>".encode("utf-8")
        document = self.html_document(body)
        bundle = build_bundle(document, body, [{
            "subject_id": "event:A",
            "predicate": "event_start_at",
            "needle": "18:00",
            "date_needle": "2026-02-31",
        }])
        self.assertEqual(bundle["facts"][0]["verification_status"], "NEEDS_REVIEW")
        self.assertEqual(bundle["facts"][0]["review_reason"], "INVALID_VALID_TIME")
        self.assertIsNone(bundle["facts"][0]["valid_time"])

    def test_hash_or_locator_change_fails_closed(self):
        body = HTML.read_bytes()
        document = self.html_document(body)
        fact = build_bundle(document, body, [{"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00"}])["facts"][0]
        altered = copy.deepcopy(fact)
        altered["locator"]["quote"] = "17:00"
        self.assertEqual(verify_fact(document, body, altered)["status"], "REJECTED")
        self.assertEqual(verify_fact(document, body + b" ", fact)["reason"], "RAW_HASH_MISMATCH")

        malformed_time_source = copy.deepcopy(fact)
        malformed_time_source["valid_time_source"] = []
        self.assertEqual(verify_fact(document, body, malformed_time_source)["reason"], "VALID_TIME_MISMATCH")

        malformed_locator = copy.deepcopy(fact)
        malformed_locator["locator"] = ["not-an-object"]
        self.assertEqual(verify_fact(document, body, malformed_locator)["reason"], "LOCATOR_MISMATCH")
        self.assertEqual(verify_fact(document, body, None)["reason"], "FACT_INPUT_INVALID")
        self.assertEqual(verify_fact({**document, "content_type": None}, body, fact)["reason"], "CONTENT_TYPE_INVALID")

    def test_locator_hashes_are_bound_for_html_and_json(self):
        html_body = HTML.read_bytes()
        html_document = self.html_document(html_body)
        html_fact = build_bundle(html_document, html_body, [{
            "subject_id": "event:A",
            "predicate": "event_start_at",
            "needle": "18:00",
        }])["facts"][0]
        altered_html = copy.deepcopy(html_fact)
        altered_html["locator"]["document_sha256"] = "0" * 64
        self.assertEqual(verify_fact(html_document, html_body, altered_html)["reason"], "LOCATOR_HASH_MISMATCH")

        json_body = JSON.read_bytes()
        json_document = acquire_document(
            source_id="S-028",
            requested_url="https://data.gov.tw/dataset/88147",
            final_url="https://data.gov.tw/dataset/88147",
            body=json_body,
            content_type="application/json",
            fetched_at=STAMP,
        )
        json_fact = extract_json_facts(json_document, json_body, [{
            "subject_id": "crime:A",
            "predicate": "count",
            "pointer": "/event/count",
            "normalizer": "integer",
        }])[0]
        altered_json = copy.deepcopy(json_fact)
        altered_json["locator"]["text_sha256"] = "0" * 64
        self.assertEqual(verify_fact(json_document, json_body, altered_json)["reason"], "LOCATOR_HASH_MISMATCH")

    def test_source_values_are_recomputed_before_confirmation(self):
        html_body = HTML.read_bytes()
        html_document = self.html_document(html_body)
        html_fact = build_bundle(html_document, html_body, [{
            "subject_id": "event:A",
            "predicate": "event_start_at",
            "needle": "18:00",
        }])["facts"][0]
        altered_html = copy.deepcopy(html_fact)
        altered_html["normalized_value"] = "17:00"
        self.assertEqual(verify_fact(html_document, html_body, altered_html)["reason"], "FACT_VALUE_MISMATCH")

        json_body = JSON.read_bytes()
        json_document = acquire_document(
            source_id="S-028",
            requested_url="https://data.gov.tw/dataset/88147",
            final_url="https://data.gov.tw/dataset/88147",
            body=json_body,
            content_type="application/json",
            fetched_at=STAMP,
        )
        json_fact = extract_json_facts(json_document, json_body, [{
            "subject_id": "crime:A",
            "predicate": "count",
            "pointer": "/event/count",
            "normalizer": "integer",
        }])[0]
        altered_json = copy.deepcopy(json_fact)
        altered_json["raw_value"] = "999"
        self.assertEqual(verify_fact(json_document, json_body, altered_json)["reason"], "FACT_VALUE_MISMATCH")

    def test_valid_time_is_recomputed_from_its_source_locator(self):
        body = HTML.read_bytes()
        document = self.html_document(body)
        fact = build_bundle(document, body, [{
            "subject_id": "event:A",
            "predicate": "event_start_at",
            "needle": "18:00",
            "date_needle": "2026-09-21",
        }])["facts"][0]
        altered = copy.deepcopy(fact)
        altered["valid_time"] = "2099-01-01"
        self.assertEqual(verify_fact(document, body, altered)["reason"], "VALID_TIME_MISMATCH")

        altered_source = copy.deepcopy(fact)
        altered_source["valid_time_source"]["quote"] = "2026-09-20"
        self.assertEqual(verify_fact(document, body, altered_source)["reason"], "VALID_TIME_MISMATCH")

        altered_identity = copy.deepcopy(fact)
        altered_identity["predicate"] = "road_control_start_at"
        self.assertEqual(verify_fact(document, body, altered_identity)["reason"], "FACT_ID_MISMATCH")

    def test_confirmation_rejects_stale_bundle_receipt(self):
        body = HTML.read_bytes()
        document = self.html_document(body)
        bundle = build_bundle(document, body, [{
            "subject_id": "event:A",
            "predicate": "event_start_at",
            "needle": "18:00",
        }])
        altered = copy.deepcopy(bundle)
        altered["facts"][0]["valid_time"] = "2099-01-01"
        with self.assertRaisesRegex(ValueError, "bundle receipt hash mismatch"):
            confirm_facts(
                altered,
                body,
                [altered["facts"][0]["fact_id"]],
                reviewer_ref="officer-1",
                verified_at=STAMP,
            )

    def test_confirmation_requires_locator_recheck_and_binds_reviewer(self):
        body = HTML.read_bytes()
        document = self.html_document(body)
        bundle = build_bundle(document, body, [{"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00"}])
        old_hash = bundle["receipt"]["bundle_sha256"]
        confirmed = confirm_facts(
            bundle,
            body,
            [bundle["facts"][0]["fact_id"]],
            reviewer_ref="officer-1",
            verified_at=STAMP,
        )
        fact = confirmed["facts"][0]
        self.assertEqual(fact["verification_status"], "CONFIRMED_OFFICIAL")
        self.assertEqual(confirmed["evidence_catalog"][0]["review"]["reviewer_ref"], "officer-1")
        self.assertEqual(confirmed["public_event_inputs"][0]["verification_status"], "CONFIRMED_OFFICIAL")
        self.assertNotEqual(confirmed["receipt"]["bundle_sha256"], old_hash)
        with self.assertRaisesRegex(ValueError, "locator verification failed"):
            confirm_facts(
                bundle,
                body + b"tampered",
                [bundle["facts"][0]["fact_id"]],
                reviewer_ref="officer-1",
                verified_at=STAMP,
            )

    def test_uncertain_time_sentences_stay_needs_review_with_distinct_reasons(self):
        body = (
            "<p>2026-09-21 原訂17:00改16:00管制</p>"
            "<p>2026-09-21 尚未決定是否提前管制</p>"
            "<p>不再於16:00管制</p>"
        ).encode("utf-8")
        document = self.html_document(body)
        bundle = build_bundle(document, body, [
            {"subject_id": "road:A", "predicate": "road_control_start_at", "needle": "原訂17:00改16:00"},
            {"subject_id": "road:B", "predicate": "road_control_start_at", "needle": "尚未決定是否提前管制"},
            {"subject_id": "road:C", "predicate": "road_control_start_at", "needle": "不再於16:00"},
        ])
        statuses = [fact["verification_status"] for fact in bundle["facts"]]
        self.assertEqual(statuses, ["NEEDS_REVIEW"] * 3)
        reasons = {fact["review_reason"] for fact in bundle["facts"]}
        self.assertEqual(reasons, {"TIME_RESCHEDULED", "TIME_UNDECIDED", "TIME_NEGATED"})
        self.assertTrue(all(fact["valid_time"] is None for fact in bundle["facts"]))

    def test_version_diff_marks_synthetic_copy_as_changed_facts(self):
        from intel_v2.located_facts import compare_document_versions
        original_body = (
            "<article><p>公告日期：2026-09-21</p><p>活動開始 18:00；道路管制 17:00。</p></article>"
        ).encode("utf-8")
        document_v1 = self.html_document(original_body)
        bundle_v1 = build_bundle(document_v1, original_body, [
            {"subject_id": "road:A", "predicate": "road_control_start_at", "needle": "17:00", "date_needle": "2026-09-21"},
        ])
        synthetic_modified_body = original_body.replace(b"17:00", b"16:00")
        document_v2 = self.html_document(synthetic_modified_body)
        bundle_v2 = build_bundle(document_v2, synthetic_modified_body, [
            {"subject_id": "road:A", "predicate": "road_control_start_at", "needle": "16:00", "date_needle": "2026-09-21"},
        ])
        self.assertEqual(document_v1["document_id"], document_v2["document_id"])
        self.assertNotEqual(document_v1["document_version_id"], document_v2["document_version_id"])
        diff = compare_document_versions(
            document_v1, bundle_v1["facts"], document_v2, bundle_v2["facts"],
            basis="SYNTHETIC_MODIFIED_COPY",
        )
        self.assertEqual(diff["basis"], "SYNTHETIC_MODIFIED_COPY")
        self.assertTrue(diff["document_version_changed"])
        affected = diff["affected_facts"]
        self.assertEqual(len(affected), 1)
        self.assertEqual(affected[0]["change"], "VALUE_CHANGED")
        self.assertEqual(affected[0]["old_normalized_value"], "17:00")
        self.assertEqual(affected[0]["new_normalized_value"], "16:00")
        all_facts = bundle_v1["facts"] + bundle_v2["facts"]
        self.assertTrue(all(
            fact["verification_status"] in {"FACT_CANDIDATE", "NEEDS_REVIEW"} for fact in all_facts
        ))

    def test_marker_separated_from_time_by_space_still_uncertain(self):
        body = (
            "<p>2026-09-21 原訂：延後至 16:00</p>"
            "<p>2026-09-21 尚未確定是否提前 16:00</p>"
            "<p>不再於 16:00</p>"
        ).encode("utf-8")
        document = self.html_document(body)
        bundle = build_bundle(document, body, [
            {"subject_id": "road:A", "predicate": "road_control_start_at", "needle": "16:00"},
            {"subject_id": "road:B", "predicate": "road_control_start_at", "needle": "尚未確定是否提前 16:00"},
            {"subject_id": "road:C", "predicate": "road_control_start_at", "needle": "不再於 16:00"},
        ])
        reasons = {fact["review_reason"] for fact in bundle["facts"]}
        self.assertEqual(reasons, {"TIME_RESCHEDULED", "TIME_UNDECIDED", "TIME_NEGATED"})
        self.assertTrue(all(fact["verification_status"] == "NEEDS_REVIEW" for fact in bundle["facts"]))

    def test_version_diff_reports_added_removed_and_identity_mismatch(self):
        from intel_v2.located_facts import compare_document_versions
        original_body = (
            "<article><p>公告日期：2026-09-21</p><p>活動開始 18:00；道路管制 17:00。</p></article>"
        ).encode("utf-8")
        document_v1 = self.html_document(original_body)
        bundle_v1 = build_bundle(document_v1, original_body, [
            {"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00", "date_needle": "2026-09-21"},
            {"subject_id": "road:A", "predicate": "road_control_start_at", "needle": "17:00", "date_needle": "2026-09-21"},
        ])
        removed_body = original_body.replace("；道路管制 17:00".encode(), "".encode())
        document_v2 = self.html_document(removed_body)
        bundle_v2 = build_bundle(document_v2, removed_body, [
            {"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00", "date_needle": "2026-09-21"},
        ])
        diff = compare_document_versions(document_v1, bundle_v1["facts"], document_v2, bundle_v2["facts"], basis="SYNTHETIC_MODIFIED_COPY")
        self.assertTrue(diff["document_version_changed"])
        self.assertEqual([a["change"] for a in diff["affected_facts"]], ["REMOVED"])
        other = acquire_document(source_id="S-028", requested_url="https://data.gov.tw/dataset/88147", final_url="https://data.gov.tw/dataset/88147", body=b"{}", content_type="application/json", fetched_at=STAMP)
        mismatch = compare_document_versions(document_v1, bundle_v1["facts"], other, [], basis="SYNTHETIC_MODIFIED_COPY")
        self.assertFalse(mismatch["source_identity_matches"])

    def test_unapproved_origin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the approved source origin"):
            acquire_document(
                source_id="S-001",
                requested_url="https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
                final_url="https://evil.example/",
                body=b"<p>safe</p>",
                content_type="text/html",
                fetched_at=STAMP,
            )

    def test_live_preflight_accepts_catalog_api_and_rejects_other_host(self):
        self.assertEqual(validate_document_url("S-028", "https://data.gov.tw/api/v2/rest/dataset/88147")["source_id"], "S-028")
        with self.assertRaisesRegex(ValueError, "outside the approved source origin"):
            validate_document_url("S-028", "https://example.invalid/dataset/88147")

    def test_live_preflight_rejects_credentials_and_nonstandard_ports(self):
        for url in (
            "https://user:data.gov.tw@data.gov.tw/api/v2/rest/dataset/88147",
            "https://data.gov.tw:8443/api/v2/rest/dataset/88147",
        ):
            with self.assertRaisesRegex(ValueError, "outside the approved source origin"):
                validate_document_url("S-028", url)


if __name__ == "__main__":
    unittest.main()
