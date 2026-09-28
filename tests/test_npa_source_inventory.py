import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "npa" / "batch1.json"
INVENTORY = ROOT / "docs" / "govintel" / "npa-source-inventory.v1.json"
CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"


def load_script(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


inventory_script = load_script("npa_source_inventory", "scripts/npa-source-inventory.py")
fusion = load_script("public_event_fusion_for_npa", "scripts/public-event-fusion.py")
from intel_v2 import npa_source_adapters as adapters


class NpaSourceInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    def test_inventory_covers_batches_and_catalog_bindings(self):
        summary = inventory_script.validate_inventory(self.inventory, self.catalog)
        self.assertGreaterEqual(summary["batch_counts"]["BATCH_1"], 5)
        self.assertGreaterEqual(summary["batch_counts"]["BATCH_2"], 11)
        self.assertGreaterEqual(summary["batch_counts"]["BATCH_3"], 9)
        self.assertGreaterEqual(summary["role_counts"]["EXCLUDE_OR_AGGREGATE_ONLY"], 3)
        canary = inventory_script.fixture_canary_report(self.inventory, FIXTURE)
        self.assertEqual(canary["mode"], "OFFLINE_FIXTURE")
        self.assertGreater(canary["records"], 0)
        self.assertEqual(canary["parse_failures"], 0)
        self.assertEqual(canary["schema_drift"], 0)

    def test_invalid_excluded_row_cannot_become_public(self):
        broken = copy.deepcopy(self.inventory)
        row = next(item for item in broken["sources"] if item["inventory_id"] == "NPA-14420")
        row["public_feed_allowed"] = True
        with self.assertRaisesRegex(ValueError, "excluded source"):
            inventory_script.validate_inventory(broken, self.catalog)
        with self.assertRaisesRegex(ValueError, "personal/case-level"):
            adapters.assert_public_canonical_allowed(self.fixture["personal_record"])

    def test_assembly_parser_fuses_with_taichung_traffic_fixture(self):
        documents = adapters.parse_assembly_records(
            self.fixture["assembly"], observed_at="2026-09-21T09:00:00+08:00"
        )
        traffic = copy.deepcopy(self.fixture["traffic"])
        traffic["named_event_id"] = documents[0]["named_event_id"]
        events = fusion.fuse_documents([documents[0], traffic])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["fusion_status"], "CONFIRMED")
        self.assertIn("start", documents[0]["semantic_fields"] or {})

    def test_assembly_time_or_route_revision_is_semantic(self):
        first = adapters.parse_assembly_records(
            self.fixture["assembly"], observed_at="2026-09-21T09:00:00+08:00"
        )[0]
        revised_row = copy.deepcopy(self.fixture["assembly"])[0]
        revised_row["event_end_at"] = "2026-09-25T13:00:00+08:00"
        revised = adapters.parse_assembly_records(
            [revised_row], observed_at="2026-09-21T10:00:00+08:00"
        )[0]
        change = adapters.compare_assembly_versions(first, revised)
        self.assertEqual(change["change_type"], "MATERIAL_CHANGE")
        self.assertIn("event_end_at", change["changed_fields"])

    def test_statistics_preserve_period_table_and_official_note(self):
        records = adapters.parse_important_statistics_rows(
            self.fixture["statistics"], observed_at="2026-09-21T09:00:00+08:00"
        )
        self.assertEqual(records[0]["period"], "2026-08")
        self.assertEqual(records[0]["table_id"], "治安-01")
        self.assertIn("年度統計", records[0]["official_notes"])
        self.assertFalse(records[0]["realtime_allowed"])

    def test_fraud_effectiveness_is_period_bound_reference_data(self):
        records = adapters.parse_fraud_effectiveness_rows(
            self.fixture["fraud_effectiveness"], observed_at="2026-09-21T09:00:00+08:00"
        )
        self.assertEqual(records[0]["period"], "2026-08")
        self.assertEqual(records[0]["metrics"]["groups"], 10)
        self.assertFalse(records[0]["realtime_allowed"])

    def test_165_family_dedupes_same_domain_and_keeps_supersession(self):
        old = adapters.parse_165_records(self.fixture["old_fraud_domains"], dataset_id="160055")
        current = adapters.parse_165_records(self.fixture["current_fraud_domains"], dataset_id="176455")
        merged = adapters.dedupe_165_records([*old, *current])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["dataset_ids"], ["160055", "176455"])
        self.assertEqual(merged[0]["superseded_by_source_id"], "CTX-165")
        rumor = adapters.parse_165_records(self.fixture["rumors"], dataset_id="38262")
        self.assertEqual(rumor[0]["local_case_count_allowed"], False)

    def test_local_and_national_accident_paths_make_one_event(self):
        def accident(source_id):
            return {
                "document_id": f"{source_id}:a1-1",
                "document_version_id": f"{source_id}:a1-1:v1",
                "source_id": source_id,
                "independent_source_id": source_id,
                "authority": "official",
                "title": "A1 事故 reference",
                "event_type": "traffic_accident",
                "named_event_id": "accident:A1-1",
                "event_start_at": "2026-09-20T08:00:00+08:00",
                "event_end_at": "2026-09-20T08:30:00+08:00",
                "district_id": "TAICHUNG-WEST",
                "agency_ids": ["NPA"],
                "location_ids": ["road:1"],
                "official_url": "https://example.gov/accident",
            }

        events = fusion.fuse_documents([accident("NPA-12818"), accident("S-034")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "traffic_accident")

    def test_live_metadata_receipt_binds_dataset_and_resource_identity(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self, url, payload):
                self.url = url
                self.body = json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return self.body

            def geturl(self):
                return self.url

            def getcode(self):
                return self.status

        class Opener:
            def __init__(self, payloads):
                self.payloads = payloads

            def open(self, request, timeout):
                return Response(request.full_url, self.payloads[request.full_url])

        row = next(item for item in self.inventory["sources"] if item["inventory_id"] == "NPA-11307")
        payload = {
            "success": True,
            "result": {
                "datasetId": "11307",
                "title": "集會遊行資訊",
                "distribution": [{
                    "resourceFormat": "CSV",
                    "resourceDownloadUrl": "https://opdadm.moi.gov.tw/api/v1/resource/RESOURCE-1/download",
                    "resourceField": [{"name": "actStTime"}],
                }],
            },
        }
        url = "https://data.gov.tw/api/v2/rest/dataset/11307"
        report = inventory_script.live_metadata_report(
            {"sources": [row]},
            source_ids=["NPA-11307"],
            opener=Opener({url: payload}),
            observed_at="2026-09-21T12:00:00+00:00",
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["failed_count"], 0)
        self.assertEqual(report["resource_count"], 1)
        self.assertEqual(report["records"][0]["resources"][0]["resource_id"], "RESOURCE-1")
        self.assertRegex(report["records"][0]["raw_sha256"], r"^[0-9a-f]{64}$")

    def test_live_metadata_failure_is_not_reported_as_zero_success(self):
        class Opener:
            def open(self, request, timeout):
                raise OSError("upstream unavailable")

        row = next(item for item in self.inventory["sources"] if item["inventory_id"] == "NPA-11307")
        report = inventory_script.live_metadata_report({"sources": [row]}, opener=Opener())
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["records"][0]["status"], "FAILED")
        self.assertNotEqual(report["observed_count"], 0)

    def test_live_metadata_without_dataset_id_is_not_success(self):
        row = next(item for item in self.inventory["sources"] if not item.get("dataset_id"))
        report = inventory_script.live_metadata_report({"sources": [row]})
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["eligible_count"], 0)
        self.assertEqual(report["skipped_count"], 1)


if __name__ == "__main__":
    unittest.main()
