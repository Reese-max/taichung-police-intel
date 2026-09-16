import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "entity-registry.py"
spec = importlib.util.spec_from_file_location("entity_registry", SCRIPT)
er = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(er)


class EntityRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = er.load_registry()

    def test_core_police_aliases_resolve_to_one_id(self):
        ids = {
            er.resolve(self.registry, "agency", name, "臺中市")["entity_id"]
            for name in ("臺中市政府警察局", "臺中市警局", "中市警")
        }
        self.assertEqual(ids, {"agency:tc-police"})

    def test_tai_and_taiwan_normalization_is_deterministic(self):
        road = er.resolve(self.registry, "location", "台灣大道", "台中市")
        self.assertEqual(road["status"], "RESOLVED")
        self.assertEqual(road["entity_id"], "location:tc-taiwan-blvd")
        district = er.resolve(self.registry, "location", "台中市西屯區", "台中市")
        self.assertEqual(district["entity_id"], "location:tc-xitun")

    def test_unknown_text_does_not_fuzzy_merge(self):
        result = er.resolve(self.registry, "location", "臺灣大到", "臺中市")
        self.assertEqual(result["status"], "NO_MATCH")

    def test_same_road_name_across_jurisdictions_is_ambiguous_without_scope(self):
        registry = {
            "schema_version": 1,
            "registry_version": 99,
            "entities": [
                {"entity_id": "location:a", "kind": "location", "canonical_label": "中正路", "aliases": [], "jurisdiction": "甲市", "status": "CONFIRMED"},
                {"entity_id": "location:b", "kind": "location", "canonical_label": "中正路", "aliases": [], "jurisdiction": "乙市", "status": "CONFIRMED"},
            ],
        }
        result = er.resolve(registry, "location", "中正路")
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertEqual(result["candidate_ids"], ["location:a", "location:b"])
        scoped = er.resolve(registry, "location", "中正路", "乙市")
        self.assertEqual(scoped["entity_id"], "location:b")

    def test_alias_collision_in_same_kind_and_jurisdiction_fails_closed(self):
        registry = {
            "schema_version": 1,
            "registry_version": 2,
            "entities": [
                {"entity_id": "agency:a", "kind": "agency", "canonical_label": "甲局", "aliases": ["共同縮寫"], "jurisdiction": "臺中市", "status": "CONFIRMED"},
                {"entity_id": "agency:b", "kind": "agency", "canonical_label": "乙局", "aliases": ["共同縮寫"], "jurisdiction": "臺中市", "status": "CONFIRMED"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "alias collision"):
            er.validate_registry(registry)

    def test_unconfirmed_alias_cannot_enter_canonical_registry(self):
        registry = {
            "schema_version": 1,
            "registry_version": 2,
            "entities": [
                {"entity_id": "agency:a", "kind": "agency", "canonical_label": "甲局", "aliases": [], "jurisdiction": "臺中市", "status": "CANDIDATE"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            er.validate_registry(registry)

    def test_person_registry_is_not_allowed(self):
        registry = {
            "schema_version": 1,
            "registry_version": 2,
            "entities": [
                {"entity_id": "person:x", "kind": "person", "canonical_label": "某人", "aliases": [], "jurisdiction": "", "status": "CONFIRMED"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "unsupported entity kind"):
            er.validate_registry(registry)

    def test_registry_hash_and_resolution_receipt_are_stable(self):
        first = er.resolve(self.registry, "agency", "中市警", "臺中市")
        second = er.resolve(self.registry, "agency", "中市警", "臺中市")
        self.assertEqual(first["registry_hash"], second["registry_hash"])
        self.assertEqual(first["registry_version"], 1)
        self.assertEqual(first["match_method"], "EXACT_NORMALIZED_ALIAS")


if __name__ == "__main__":
    unittest.main()
