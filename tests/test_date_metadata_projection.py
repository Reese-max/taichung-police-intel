import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("date_metadata_query_store", ROOT / "scripts/query-store.py")
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)
gateway_spec = importlib.util.spec_from_file_location("date_metadata_query_gateway", ROOT / "scripts/query-gateway.py")
gateway = importlib.util.module_from_spec(gateway_spec)
gateway_spec.loader.exec_module(gateway)
FIXTURE = json.loads((ROOT / "tests/fixtures/date-provenance/revision-metadata.json").read_text())


class DateMetadataProjectionTests(unittest.TestCase):
    def test_revision_provenance_survives_without_a_publication_date_or_excerpt(self):
        item = qs.project_feed_item(FIXTURE["item"], "a" * 64)
        self.assertIsNone(item["published_at"])
        self.assertEqual(item["document_revision_at"], FIXTURE["item"]["document_revision_at"])
        self.assertNotIn("excerpt", item["date_evidence"])
        # The PDF evidence differs from the canonical row's DOC URL/payload hash.
        self.assertNotEqual(item["date_evidence"]["official_url"], item["official_url"])
        self.assertNotEqual(item["date_evidence"]["content_sha256"], item["content_sha256"])
        source = qs.project_source(FIXTURE["source"], "b" * 64)
        self.assertEqual(source["data_as_of_evidence"], item["date_evidence"])
        self.assertEqual(source["data_as_of_scope"], "LATEST_EVIDENCED_DOCUMENT_VERSION")

    def test_malformed_or_unapproved_revision_evidence_is_refused(self):
        mutations = {
            "origin": {"official_url": "https://unapproved.example.test/fixture.pdf"},
            "credentials": {"official_url": "https://user:secret@www.tccc.gov.tw/fixture.pdf"},
            "non-pdf": {"official_url": "https://www.tccc.gov.tw/fixture.doc"},
            "hash": {"content_sha256": "WRONG"},
            "page zero": {"page_number": 0},
            "page boolean": {"page_number": True},
            "date mismatch": {"document_revision_at": "2026-08-28T00:00:00+08:00"},
            "timezone": {"document_revision_at": "2026-08-27T00:00:00"},
            "calendar": {"document_revision_at": "2026-02-30T00:00:00+08:00"},
            "private field": {"private_notes": "FICTIONAL_PRIVATE"},
            "nested excerpt": {"excerpt": {"body": "FICTIONAL_PRIVATE"}},
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                item = copy.deepcopy(FIXTURE["item"])
                item["date_evidence"].update(mutation)
                with self.assertRaises(ValueError):
                    qs.project_feed_item(item, "a" * 64)

    def test_revision_requires_complete_provenance(self):
        for missing in ("document_revision_at", "date_basis", "date_evidence"):
            with self.subTest(missing=missing):
                item = copy.deepcopy(FIXTURE["item"])
                del item[missing]
                with self.assertRaises(ValueError):
                    qs.project_feed_item(item, "a" * 64)

    def test_source_date_scope_cannot_claim_a_different_date_contract(self):
        for scope in ("COLLECTION_WINDOW", "UNKNOWN_SCOPE", {"private": "FICTIONAL"}):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                qs.project_source(dict(FIXTURE["source"], data_as_of_scope=scope), "a" * 64)
        api = dict(FIXTURE["source"], data_as_of_basis="OFFICIAL_API_RECORD_DATE",
                   data_as_of_evidence=None, data_as_of_scope="OBSERVED_API_PAGE")
        self.assertEqual(qs.project_source(api, "a" * 64)["data_as_of_basis"], "OFFICIAL_API_RECORD_DATE")
        titled = dict(FIXTURE["source"], data_as_of_basis="OFFICIAL_LIST_TITLE_DATE", data_as_of_evidence=None)
        self.assertEqual(qs.project_source(titled, "a" * 64)["data_as_of_scope"], "LATEST_EVIDENCED_DOCUMENT_VERSION")

    def test_health_endpoint_projection_preserves_closed_revision_evidence(self):
        source = copy.deepcopy(FIXTURE["source"])
        source.update(qs._project_source_dates(source))
        projected = gateway._public_source(source)
        self.assertEqual(projected["data_as_of_evidence"], source["data_as_of_evidence"])
        self.assertEqual(projected["data_as_of_basis"], source["data_as_of_basis"])
        self.assertEqual(projected["data_as_of_scope"], source["data_as_of_scope"])
        self.assertNotIn("excerpt", projected["data_as_of_evidence"])
        source["data_as_of_evidence"]["private_notes"] = "FICTIONAL_PRIVATE"
        with self.assertRaises(gateway.GatewayError):
            gateway._public_source(source)

    def test_evidence_catalog_never_uses_observation_or_revision_as_publication(self):
        item = qs.project_feed_item(FIXTURE["item"], "a" * 64)
        source = qs.project_source(FIXTURE["source"], "b" * 64)
        evidence = gateway.QueryGateway._trusted_evidence_catalog({"items": [item], "sources": [source]})[0]
        self.assertIsNone(evidence["published_at"])
        self.assertEqual(evidence["observed_at"], item["fetched_at"])
        self.assertEqual(evidence["document_revision_at"], item["document_revision_at"])
        self.assertEqual(evidence["date_basis"], item["date_basis"])
        self.assertFalse(evidence["is_current"])
        undated = copy.deepcopy(item)
        for key in ("document_revision_at", "date_basis", "date_evidence"):
            del undated[key]
        unknown = gateway.QueryGateway._trusted_evidence_catalog({"items": [undated], "sources": [source]})[0]
        self.assertIsNone(unknown["published_at"])
        self.assertNotIn("document_revision_at", unknown)


if __name__ == "__main__":
    unittest.main()
