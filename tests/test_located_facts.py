from __future__ import annotations

import copy
from pathlib import Path
import unittest

from intel_v2.located_facts import acquire_document, build_bundle, extract_json_facts, validate_document_url, verify_fact


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

    def test_hash_or_locator_change_fails_closed(self):
        body = HTML.read_bytes()
        document = self.html_document(body)
        fact = build_bundle(document, body, [{"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00"}])["facts"][0]
        altered = copy.deepcopy(fact)
        altered["locator"]["quote"] = "17:00"
        self.assertEqual(verify_fact(document, body, altered)["status"], "REJECTED")
        self.assertEqual(verify_fact(document, body + b" ", fact)["reason"], "RAW_HASH_MISMATCH")

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


if __name__ == "__main__":
    unittest.main()
