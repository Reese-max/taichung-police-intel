import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-query-domain.py"
spec = importlib.util.spec_from_file_location("build_query_domain", SCRIPT)
builder = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(builder)


def event():
    return {
        "schema_version": 1,
        "public_event_id": "PE-BUILD-1",
        "canonical_title": "大型活動交通管制",
        "event_date": "2026-09-20",
        "fusion_status": "CONFIRMED",
        "linked_document_versions": [{
            "document_id": "DOC-BUILD-1",
            "document_version_id": "DOC-BUILD-1:v1",
            "source_id": "S-001",
            "official_url": "https://police.example/event",
        }],
    }


def statistic():
    return {
        "statistic_id": "STAT-BUILD-1",
        "dataset_id": "NPA-STAT-1",
        "source_id": "S-028",
        "metric": "fraud_reports",
        "period": "2026-08",
        "value": 12,
        "unit": "件",
        "geography": "臺中市",
        "provisional": False,
        "updated_at": "2026-09-21T00:00:00+08:00",
        "official_url": "https://data.example/statistics/1",
    }


class QueryDomainBuilderTests(unittest.TestCase):
    def test_build_is_deterministic_and_writes_validated_stores(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events_input = root / "events.json"
            stats_input = root / "statistics.json"
            events_output = root / "events.store.json"
            stats_output = root / "statistics.store.json"
            events_input.write_text(json.dumps({"public_events": [event()]}), encoding="utf-8")
            stats_input.write_text(json.dumps({"statistics": [statistic()]}), encoding="utf-8")

            stores = builder.build_stores(public_events=events_input, statistics=stats_input)
            self.assertEqual(
                builder.main([
                    "build",
                    "--public-events", str(events_input),
                    "--public-events-output", str(events_output),
                ]),
                0,
            )
            builder.write_store(stats_output, stores["statistics"])
            first_events = events_output.read_bytes()
            first_stats = stats_output.read_bytes()
            builder.write_store(events_output, builder.build_stores(public_events=events_input)["public_events"])
            builder.write_store(stats_output, builder.build_stores(statistics=stats_input)["statistics"])

            self.assertEqual(first_events, events_output.read_bytes())
            self.assertEqual(first_stats, stats_output.read_bytes())
            self.assertEqual(json.loads(first_events)["store_type"], "PUBLIC_EVENT_QUERY")
            self.assertEqual(json.loads(first_stats)["store_type"], "TYPED_STATISTICS_QUERY")

    def test_fixture_only_demo_is_not_accepted_as_canonical_events(self):
        with self.assertRaisesRegex(ValueError, "public_events must be a bounded array"):
            builder.build_stores(public_events=ROOT / "apps/web/public/data/public-event-demo.json")

    def test_each_input_requires_a_matching_output(self):
        with self.assertRaises(SystemExit):
            builder.main(["build", "--public-events", "events.json"])
        with self.assertRaises(SystemExit):
            builder.main(["build", "--public-events", "events.json", "--public-events-output", "events.json"])


if __name__ == "__main__":
    unittest.main()
