import importlib.util
from pathlib import Path
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

    @staticmethod
    def small_registry():
        return {
            "schema_version": 1,
            "registry_version": 1,
            "updated_at": "2026-09-21",
            "entities": [{
                "entity_id": "location:demo",
                "kind": "location",
                "canonical_label": "甲路",
                "aliases": ["乙路", "丙路"],
                "jurisdiction": "臺中市",
                "status": "CONFIRMED",
                "evidence": "fixture:demo",
            }],
        }

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

    def test_unknown_text_does_not_fuzzy_merge_and_has_registry_receipt(self):
        result = er.resolve(self.registry, "location", "臺灣大到", "臺中市")
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["registry_version"], self.registry["registry_version"])
        self.assertEqual(result["registry_hash"], er.registry_hash(self.registry))

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
        self.assertEqual(result["registry_version"], 99)
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

    def test_scalar_aliases_fail_closed_instead_of_becoming_characters(self):
        registry = {
            "schema_version": 1,
            "registry_version": 2,
            "entities": [
                {"entity_id": "agency:a", "kind": "agency", "canonical_label": "甲局", "aliases": "共同縮寫", "jurisdiction": "臺中市", "status": "CONFIRMED"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "aliases must be an array"):
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

    def test_unscoped_no_match_also_has_registry_identity(self):
        result = er.resolve(self.registry, "named_event", "不存在活動")
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["registry_version"], 1)
        self.assertEqual(result["registry_hash"], er.registry_hash(self.registry))

    def test_same_named_event_on_different_dates_never_shares_identity(self):
        registry = {
            "schema_version": 1,
            "registry_version": 2,
            "entities": [
                {"entity_id": "named_event:a", "kind": "named_event", "canonical_label": "市政論壇", "aliases": [], "jurisdiction": "臺中市", "event_date": "2026-09-21", "status": "CONFIRMED"},
                {"entity_id": "named_event:b", "kind": "named_event", "canonical_label": "市政論壇", "aliases": [], "jurisdiction": "臺中市", "event_date": "2026-09-22", "status": "CONFIRMED"},
            ],
        }
        self.assertEqual(er.resolve(registry, "named_event", "市政論壇", "臺中市")["status"], "AMBIGUOUS")
        self.assertEqual(
            er.resolve(registry, "named_event", "市政論壇", "臺中市", "2026-09-21")["entity_id"],
            "named_event:a",
        )
        self.assertEqual(
            er.resolve(registry, "named_event", "市政論壇", "臺中市", "2026-09-23")["status"],
            "NO_MATCH",
        )

    def test_named_event_requires_an_iso_date(self):
        registry = {
            "schema_version": 1,
            "registry_version": 1,
            "entities": [{
                "entity_id": "named_event:missing-date",
                "kind": "named_event",
                "canonical_label": "市政論壇",
                "aliases": [],
                "jurisdiction": "臺中市",
                "status": "CONFIRMED",
            }],
        }
        with self.assertRaisesRegex(ValueError, "event_date"):
            er.validate_registry(registry)

    def test_manual_correction_keeps_alias_evidence_and_increments_identity(self):
        changed = er.correct_alias(
            self.registry,
            "agency:tc-police",
            "臺中警察局",
            evidence="operator-note:alias-1",
            operator="reviewer-1",
            decided_at="2026-09-21T10:00:00+08:00",
        )
        self.assertEqual(changed["registry_version"], 2)
        self.assertEqual(er.resolve(changed, "agency", "臺中警察局", "臺中市")["entity_id"], "agency:tc-police")
        audit = changed["audit_history"][0]
        self.assertEqual(audit["action"], "CORRECTION")
        self.assertIn("臺中警察局", audit["payload"]["after"]["entity"]["aliases"])
        er.validate_registry(changed)

    def test_manual_merge_retires_source_but_keeps_full_before_snapshot(self):
        registry = {
            "schema_version": 1,
            "registry_version": 4,
            "entities": [
                {"entity_id": "agency:a", "kind": "agency", "canonical_label": "甲局", "aliases": ["甲"], "jurisdiction": "臺中市", "status": "CONFIRMED", "evidence": "a"},
                {"entity_id": "agency:b", "kind": "agency", "canonical_label": "乙局", "aliases": ["乙"], "jurisdiction": "臺中市", "status": "CONFIRMED", "evidence": "b"},
            ],
        }
        changed = er.merge_entities(
            registry,
            "agency:a",
            "agency:b",
            evidence="operator-note:merge-1",
            operator="reviewer-1",
            decided_at="2026-09-21T10:01:00+08:00",
        )
        self.assertEqual(len(changed["entities"]), 1)
        self.assertEqual(er.resolve(changed, "agency", "乙局", "臺中市")["entity_id"], "agency:a")
        self.assertEqual(changed["audit_history"][0]["payload"]["before"]["source"]["evidence"], "b")

    def test_manual_split_partitions_names_and_keeps_retired_snapshot(self):
        registry = self.small_registry()
        changed = er.split_entity(
            registry,
            "location:demo",
            [
                {"entity_id": "location:demo-a", "canonical_label": "甲路", "aliases": ["乙路"]},
                {"entity_id": "location:demo-b", "canonical_label": "丙路", "aliases": []},
            ],
            evidence="operator-note:split-1",
            operator="reviewer-1",
            decided_at="2026-09-21T10:02:00+08:00",
        )
        self.assertEqual({item["entity_id"] for item in changed["entities"]}, {"location:demo-a", "location:demo-b"})
        self.assertEqual(er.resolve(changed, "location", "乙路", "臺中市")["entity_id"], "location:demo-a")
        self.assertEqual(changed["audit_history"][0]["payload"]["before"]["entity"]["entity_id"], "location:demo")

    def test_tampered_manual_audit_fails_closed(self):
        changed = er.correct_alias(
            self.registry,
            "agency:tc-police",
            "臺中警察局",
            evidence="operator-note:alias-2",
            operator="reviewer-1",
            decided_at="2026-09-21T10:00:00+08:00",
        )
        changed["audit_history"][0]["payload"]["evidence"] = "tampered"
        with self.assertRaisesRegex(ValueError, "audit hash mismatch"):
            er.validate_registry(changed)


if __name__ == "__main__":
    unittest.main()
