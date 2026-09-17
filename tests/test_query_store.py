import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "query-store.py"
spec = importlib.util.spec_from_file_location("query_store", SCRIPT)
qs = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(qs)


class QueryStoreTests(unittest.TestCase):
    def test_checked_in_publication_rebuild_is_deterministic(self):
        first = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        second = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertEqual(first["generation_id"], second["generation_id"])
        self.assertEqual(first["items"], second["items"])
        self.assertGreater(first["counts"]["publication_items"], 0)
        self.assertGreater(first["counts"]["sources"], 0)
        self.assertTrue(all(row["name"] for row in first["sources"]))

    def test_every_projected_item_has_a_canonical_backlink(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        for row in store["items"]:
            self.assertEqual(row["trust_tier"], "CANONICAL_PUBLICATION")
            self.assertEqual(row["canonical_ref"]["artifact"], "intelligence-feed.json")
            self.assertEqual(row["canonical_ref"]["stable_id"], row["canonical_id"])
            self.assertEqual(len(row["canonical_ref"]["artifact_sha256"]), 64)
            self.assertNotIn("payload", row)
            self.assertNotIn("raw", row)

    def test_source_projection_uses_canonical_source_name(self):
        status, status_hash = qs.load_json(qs.DEFAULT_STATUS)
        source = status["sources"][0]
        projected = qs.project_source(source, status_hash)
        self.assertEqual(projected["name"], source["source_name"])
        malformed = dict(source)
        malformed.pop("source_name", None)
        with self.assertRaisesRegex(ValueError, "source_name"):
            qs.project_source(malformed, status_hash)

    def test_structured_query_is_bounded_and_generation_bound(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        result = qs.query_store(store, source_id="S-004", limit=1)
        self.assertEqual(result["query_generation_id"], store["generation_id"])
        self.assertLessEqual(result["result_count"], 1)
        self.assertTrue(all(row["source_id"] == "S-004" for row in result["results"]))
        self.assertEqual(result["truncated"], result["total_matches"] > result["result_count"])

    def test_query_limit_fails_closed(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        for bad in (0, 101, 1000):
            with self.assertRaisesRegex(ValueError, "limit"):
                qs.query_store(store, limit=bad)

    def test_duplicate_canonical_ids_are_rejected(self):
        feed, feed_hash = qs.load_json(qs.DEFAULT_FEED)
        status, status_hash = qs.load_json(qs.DEFAULT_STATUS)
        brief, brief_hash = qs.load_json(qs.DEFAULT_BRIEF)
        feed = json.loads(json.dumps(feed))
        feed["items"].append(dict(feed["items"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate canonical_id"):
            qs.build_store(feed, status, brief, {"feed": feed_hash, "status": status_hash, "brief": brief_hash})

    def test_missing_identity_fails_closed(self):
        feed, feed_hash = qs.load_json(qs.DEFAULT_FEED)
        status, status_hash = qs.load_json(qs.DEFAULT_STATUS)
        brief, brief_hash = qs.load_json(qs.DEFAULT_BRIEF)
        feed = json.loads(json.dumps(feed))
        del feed["items"][0]["stable_id"]
        with self.assertRaisesRegex(ValueError, "stable_id"):
            qs.build_store(feed, status, brief, {"feed": feed_hash, "status": status_hash, "brief": brief_hash})

    def test_atomic_output_is_valid_json(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "query-store.json"
            qs.atomic_write_json(output, store)
            loaded = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(loaded["generation_id"], store["generation_id"])


if __name__ == "__main__":
    unittest.main()
