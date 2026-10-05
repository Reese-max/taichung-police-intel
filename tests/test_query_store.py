import importlib.util
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from governed_policy_fixture import make_governed_policy_fixture, load_fixture_module, bind_checked_in_publication

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "query-store.py"
spec = importlib.util.spec_from_file_location("query_store", SCRIPT)
qs = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(qs)


class QueryStoreTests(unittest.TestCase):
    def setUp(self):
        global qs
        temporary = tempfile.TemporaryDirectory(prefix="fictional-query-positive-")
        self.addCleanup(temporary.cleanup)
        fixture = bind_checked_in_publication(make_governed_policy_fixture(temporary.name))
        qs = load_fixture_module(fixture, "fictional_query_positive", "scripts/query-store.py")

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
            self.assertEqual(row["source_role"], "PRIMARY_OFFICIAL")
            self.assertEqual(row["canonical_ref"]["artifact"], "intelligence-feed.json")
            self.assertEqual(row["canonical_ref"]["stable_id"], row["canonical_id"])
            self.assertEqual(len(row["canonical_ref"]["artifact_sha256"]), 64)
            self.assertEqual(row["canonical_ref"]["evidence_id"], f"PUB-{row['canonical_id']}")
            self.assertEqual(
                row["canonical_ref"]["document_version_id"],
                f"DOCV-{row['content_sha256'][:20].upper()}",
            )
            self.assertIn(row["verification_status"], {"VERIFIED", "STALE"})
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

    def test_item_without_own_freshness_inherits_the_source_row(self):
        item = {
            "stable_id": "PE-FRESHNESS-FALLBACK",
            "title": "無自有時效的公告項目",
            "source_id": "S-004",
            "official_url": "https://www.police.taichung.gov.tw/news/1",
            "evidence_count": 1,
            "content_sha256": "c" * 64,
        }
        # The projected status and the evidence catalog resolve freshness the same
        # way, so an item row can never be labelled STALE while its source-backed
        # evidence row is treated as current.
        self.assertEqual(qs.project_feed_item(item, "a" * 64)["verification_status"], "STALE")
        self.assertEqual(
            qs.project_feed_item(item, "a" * 64, source_freshness={"S-004": "FRESH"})["verification_status"], "VERIFIED"
        )
        self.assertEqual(
            qs.project_feed_item(item, "a" * 64, source_freshness={"S-004": "RECENT"})["verification_status"], "VERIFIED"
        )
        self.assertEqual(
            qs.project_feed_item(item, "a" * 64, source_freshness={"S-004": "STALE"})["verification_status"], "STALE"
        )
        explicit = dict(item, freshness_status="STALE")
        self.assertEqual(
            qs.project_feed_item(explicit, "a" * 64, source_freshness={"S-004": "FRESH"})["verification_status"], "STALE"
        )

    def test_store_saved_by_a_superseded_projection_is_refused(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertEqual(store["projection_version"], qs.PROJECTION_VERSION)
        legacy = dict(store, projection_version="publication-metadata-v2")
        # Re-seal the mutation so only the projection-version contract can refuse it.
        legacy["projection_sha256"] = qs.sha256_bytes(qs.canonical_json(
            {key: value for key, value in legacy.items() if key != "projection_sha256"}
        ))
        with self.assertRaisesRegex(ValueError, "unsupported query store"):
            qs.validate_store(legacy)

    def test_exact_canonical_id_lookup_is_strict(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        canonical_id = store["items"][0]["canonical_id"]
        result = qs.query_store(store, canonical_id=canonical_id, limit=10)
        self.assertEqual([row["canonical_id"] for row in result["results"]], [canonical_id])
        self.assertEqual(result["total_matches"], 1)

        missing = qs.query_store(store, canonical_id="does-not-exist", limit=10)
        self.assertEqual(missing["results"], [])
        self.assertEqual(missing["total_matches"], 0)

    def test_query_store_is_bound_to_current_source_policy(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        policy = qs.load_current_policy()
        self.assertEqual(store["policy"]["policy_hash"], policy["policy_hash"])
        result = qs.query_store(store, source_id="S-004")
        self.assertEqual(result["policy"], store["policy"])
        self.assertEqual(result["query_coverage"]["capability_id"], "publication_metadata")
        self.assertEqual(result["query_coverage"]["policy_hash"], policy["policy_hash"])
        self.assertIn("publication_metadata", result["query_coverage"]["supported_capabilities"])

    def test_tampered_source_policy_binding_fails_closed(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        store["policy"]["policy_hash"] = "0" * 64
        store["projection_sha256"] = qs.sha256_bytes(
            qs.canonical_json({key: value for key, value in store.items() if key != "projection_sha256"})
        )
        with self.assertRaisesRegex(ValueError, "policy mismatch"):
            qs.query_store(store)

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

    def test_resealed_current_projection_cannot_publish_unadmitted_sources(self):
        native = load_fixture_module({"root": ROOT}, "native_current_query_refusal", "scripts/query-store.py")
        store = native.build_from_paths(native.DEFAULT_FEED, native.DEFAULT_STATUS, native.DEFAULT_BRIEF)
        self.assertEqual(store["items"], [])
        historical = json.loads((ROOT / "tests/fixtures/source-policy/publication-metadata-v3.json").read_text())
        for sid in ("S-999", "S-032", "S-004", None):
            with self.subTest(source_id=sid):
                row = copy.deepcopy(historical["items"][0])
                row["source_id"] = sid
                candidate = copy.deepcopy(store)
                candidate["items"] = [row]
                candidate["counts"]["publication_items"] = 1
                self._reseal(native, candidate)
                with self.assertRaises(ValueError):
                    native.query_store(candidate)

    @staticmethod
    def _reseal(module, store):
        store["projection_sha256"] = module.sha256_bytes(module.canonical_json(
            {key: value for key, value in store.items() if key != "projection_sha256"}))

    def test_resealed_governed_projection_revalidates_row_authority_and_fields(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertGreater(len(store["items"]), 0)
        mutations = {
            "unknown source": lambda row: row.update(source_id="S-999"),
            "unpromoted candidate": lambda row: row.update(source_id="S-032"),
            "full text": lambda row: row.update(full_text="FICTIONAL_FORBIDDEN_TEXT"),
            "committee": lambda row: row.update(committee="FICTIONAL_FORBIDDEN_DETAIL"),
            "nested milestone": lambda row: row.update(next_milestone={"label": "FICTIONAL_DETAIL"}),
            "nested title": lambda row: row.update(title={"full_text": "FICTIONAL_DETAIL"}),
            "nested canonical reference": lambda row: row["canonical_ref"].update(private_notes="FICTIONAL_PRIVATE"),
            "wrong canonical reference": lambda row: row["canonical_ref"].update(stable_id="WRONG_ID"),
            "wrong artifact": lambda row: row["canonical_ref"].update(artifact_sha256="0" * 64),
            "wrong document locator": lambda row: row["canonical_ref"].update(document_version_id="DOCV-WRONG"),
            "unapproved origin": lambda row: row.update(official_url="https://unapproved.example.test/fictional"),
            "credentialed approved origin": lambda row: row.update(official_url=row["official_url"].replace("https://", "https://user:secret@")),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                candidate = copy.deepcopy(store)
                mutate(candidate["items"][0])
                self._reseal(qs, candidate)
                with self.assertRaises(ValueError):
                    qs.query_store(candidate)
        self.assertEqual(qs.query_store(store)["result_count"], min(20, len(store["items"])))

    def test_resealed_current_source_health_projection_is_closed_and_bound(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        for index, mutation in enumerate((lambda row: row.update(private_notes="FICTIONAL_PRIVATE"),
                         lambda row: row.update(source_health={"full_text": "FICTIONAL_DETAIL"}),
                         lambda row: row["canonical_ref"].update(private_notes="FICTIONAL_PRIVATE"),
                         lambda row: row["canonical_ref"].update(artifact_sha256="0" * 64))):
            with self.subTest(mutation=index):
                candidate = copy.deepcopy(store)
                mutation(candidate["sources"][0])
                self._reseal(qs, candidate)
                with self.assertRaises(ValueError):
                    qs.query_store(candidate)
        self.assertEqual(len(qs.query_store(store)["source_status"]), 5)

    def test_canonical_source_health_cannot_launder_nested_payloads(self):
        feed, feed_hash = qs.load_json(qs.DEFAULT_FEED)
        status, status_hash = qs.load_json(qs.DEFAULT_STATUS)
        brief, brief_hash = qs.load_json(qs.DEFAULT_BRIEF)
        hashes = {"feed": feed_hash, "status": status_hash, "brief": brief_hash}
        for field in ("source_health", "window_completeness", "result", "freshness_status", "last_checked_at", "data_as_of"):
            with self.subTest(field=field):
                candidate = copy.deepcopy(status)
                candidate["sources"][0][field] = {"body": "FICTIONAL_NESTED_PRIVATE"}
                with self.assertRaises(ValueError):
                    qs.build_store(feed, candidate, brief, hashes)

    def test_current_query_requires_exact_canonical_bytes_even_after_reseal(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        captured = qs.canonical_artifacts_from_paths()
        self.assertGreater(qs.query_store(store, canonical_artifacts=captured)["result_count"], 0)
        mutations = [lambda s: s["items"][0].update(title="FICTIONAL_CHANGED_WITH_OLD_REFS"),
                     lambda s: s["generated_from"].update(collection_status={"body": "PRIVATE_FIXTURE"}),
                     lambda s: s["generated_from"].update(publication_status={"body": "PRIVATE_FIXTURE"}),
                     lambda s: s["generated_from"].update(snapshot_complete={"body": "PRIVATE_FIXTURE"})]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                candidate = copy.deepcopy(store)
                mutate(candidate)
                self._reseal(qs, candidate)
                for bundle in (None, captured):
                    with self.assertRaises(ValueError):
                        qs.query_store(candidate, canonical_artifacts=bundle)
        self.assertEqual(qs.query_store(store)["canonical_artifact_hashes"],
                         qs.query_store(store, canonical_artifacts=captured)["canonical_artifact_hashes"])

    def test_current_custom_cli_index_uses_explicit_canonical_artifacts(self):
        captured = qs.canonical_artifacts_from_paths()
        feed = json.loads(captured["feed"])
        feed["items"][0]["title"] = "FICTIONAL_CUSTOM_ARTIFACT_TITLE"
        custom = dict(captured, feed=qs.canonical_json(feed))
        store = qs.build_from_canonical_artifacts(custom)
        with self.assertRaisesRegex(ValueError, "canonical publication"):
            qs.query_store(store)
        result = qs.query_store(store, canonical_artifacts=custom, canonical_id=store["items"][0]["canonical_id"])
        self.assertEqual(result["result_count"], 1)
        with tempfile.TemporaryDirectory(prefix="fictional-custom-query-") as temporary:
            root = Path(temporary)
            paths = {}
            for name, raw in custom.items():
                paths[name] = root / (name + ".json")
                paths[name].write_bytes(raw)
            index = root / "query.json"
            qs.atomic_write_json(index, store, canonical_artifacts=custom)
            command = [sys.executable, str(qs.ROOT / "scripts/query-store.py"), "query", "--store", str(index)]
            refused = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(refused.returncode, 0)
            admitted = subprocess.run(command + [part for name, path in paths.items() for part in ("--" + name, str(path))],
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(admitted.returncode, 0, admitted.stderr)
            self.assertGreater(json.loads(admitted.stdout)["result_count"], 0)

    def test_current_artifact_bundle_has_no_boolean_or_partial_trust_bypass(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        captured = qs.canonical_artifacts_from_paths()
        for bundle in (True, {}, {"feed": captured["feed"]}, dict(captured, feed={"unchecked": True})):
            with self.subTest(bundle_type=type(bundle).__name__), self.assertRaises(ValueError):
                qs.query_store(store, canonical_artifacts=bundle)


if __name__ == "__main__":
    unittest.main()
