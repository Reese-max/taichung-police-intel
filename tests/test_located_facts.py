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
