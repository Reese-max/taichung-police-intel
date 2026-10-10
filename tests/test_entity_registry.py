import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "entity-registry.py"
SOURCE_CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"
spec = importlib.util.spec_from_file_location("entity_registry", SCRIPT)
er = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(er)


class EntityRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = er.load_registry(
            ROOT / "tests/fixtures/entity-registry/seeded-collision-coverage.v2.json"
            if self._testMethodName.startswith("test_shipped_") else er.DEFAULT_REGISTRY
        )

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

    def test_exact_id_precedes_alias_but_respects_kind_scope_and_date(self):
        registry = json.loads((ROOT / "tests/fixtures/entity-registry/false-positives.v1.json").read_text(encoding="utf-8"))
        exact = er.resolve(registry, "agency", "agency:tc-police-fixture", "台中市")
        self.assertEqual((exact["entity_id"], exact["match_method"]), ("agency:tc-police-fixture", "EXACT_ID"))
        self.assertEqual(er.resolve(registry, "agency", "agency:tc-police-fixture", "高雄市")["status"], "NO_MATCH")
        self.assertEqual(er.resolve(registry, "location", "agency:tc-police-fixture")["status"], "NO_MATCH")
        self.assertEqual(er.resolve(registry, "named_event", "named_event:forum-0920", "臺中市", "2026-09-21")["status"], "NO_MATCH")
        self.assertEqual(er.resolve(registry, "agency", "中市警")["status"], "AMBIGUOUS")
        self.assertEqual(er.resolve(registry, "agency", "中市警", "台中市")["entity_id"], "agency:tc-police-fixture")

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
        self.assertEqual(first["registry_version"], self.registry["registry_version"])
        self.assertEqual(first["match_method"], "EXACT_NORMALIZED_ALIAS")

    def test_unscoped_no_match_also_has_registry_identity(self):
        result = er.resolve(self.registry, "named_event", "不存在活動")
        self.assertEqual(result["status"], "NO_MATCH")
        self.assertEqual(result["registry_version"], self.registry["registry_version"])
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
        self.assertEqual(changed["registry_version"], self.registry["registry_version"] + 1)
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
        self.assertEqual(er.resolve_entity_id(changed, "agency:b")["redirect_entity_ids"], ["agency:a"])

    def test_manual_merge_redirect_can_be_reverted_without_losing_audit(self):
        registry = {
            "schema_version": 1,
            "registry_version": 4,
            "entities": [
                {"entity_id": "agency:a", "kind": "agency", "canonical_label": "甲局", "aliases": [], "jurisdiction": "臺中市", "status": "CONFIRMED", "evidence": "a"},
                {"entity_id": "agency:b", "kind": "agency", "canonical_label": "乙局", "aliases": [], "jurisdiction": "臺中市", "status": "CONFIRMED", "evidence": "b"},
            ],
        }
        merged = er.merge_entities(
            registry,
            "agency:a",
            "agency:b",
            evidence="operator-note:merge-revert",
            operator="reviewer-1",
            decided_at="2026-09-21T10:01:00+08:00",
        )
        reverted = er.revert_manual_change(
            merged,
            1,
            evidence="operator-note:revert-1",
            operator="reviewer-1",
            decided_at="2026-09-21T10:03:00+08:00",
        )
        self.assertEqual({item["entity_id"] for item in reverted["entities"]}, {"agency:a", "agency:b"})
        self.assertEqual(er.resolve_entity_id(reverted, "agency:b")["status"], "ACTIVE")
        self.assertEqual([item["action"] for item in reverted["audit_history"]], ["MERGE", "REVERT"])
        with self.assertRaisesRegex(ValueError, "no longer matches merge"):
            er.revert_manual_change(
                reverted,
                1,
                evidence="operator-note:revert-twice",
                operator="reviewer-1",
                decided_at="2026-09-21T10:04:00+08:00",
            )
        er.validate_registry(reverted)

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
        self.assertEqual(er.resolve_entity_id(changed, "location:demo")["redirect_entity_ids"], ["location:demo-a", "location:demo-b"])
        self.assertEqual(changed["audit_history"][0]["payload"]["before"]["entity"]["entity_id"], "location:demo")

        reverted = er.revert_manual_change(
            changed,
            1,
            evidence="operator-note:split-revert",
            operator="reviewer-1",
            decided_at="2026-09-21T10:04:00+08:00",
        )
        self.assertEqual({item["entity_id"] for item in reverted["entities"]}, {"location:demo"})
        self.assertEqual(er.resolve_entity_id(reverted, "location:demo")["status"], "ACTIVE")
        self.assertEqual(reverted["audit_history"][-1]["action"], "REVERT")
        er.validate_registry(reverted)

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

    # Shipped-fixture regressions: the false positives the registry exists to
    # prevent must be present in the shipped data, not only in synthetic rows.
    CORE_AGENCIES = {
        "agency:tc-police": ("臺中市政府警察局", "中市警"),
        "agency:tc-traffic": ("臺中市政府交通局", "中市交通局"),
        "agency:tc-fire": ("臺中市政府消防局", "中市消防局"),
        "agency:tc-council": ("臺中市議會", "中市議會"),
        "agency:tc-rdec": ("臺中市政府研究發展考核委員會", "研考會"),
    }
    NAMED_EVENTS = {
        ("臺中市政府跨年晚會", "2026-12-31"): "named_event:tc-new-year-eve-2026",
        ("臺中市政府跨年晚會", "2027-12-31"): "named_event:tc-new-year-eve-2027",
        ("臺中市議會定期會", "2026-10-06"): "named_event:tc-council-regular-2026q4",
        ("臺中市議會定期會", "2027-03-09"): "named_event:tc-council-regular-2027q1",
    }
    SAME_NAME_EVENT_DATES = {
        "臺中市政府跨年晚會": ("2026-12-31", "2027-12-31"),
        "臺中市議會定期會": ("2026-10-06", "2027-03-09"),
    }
    SAME_NAME_ROADS = {
        "中正路": ("臺中市", "location:tc-zhongzheng-road", "新北市", "location:nt-zhongzheng-road"),
        "中山路": ("臺中市", "location:tc-zhongshan-road", "臺南市", "location:tn-zhongshan-road"),
    }
    VENUES = {
        "location:tc-city-hall": ("臺中市政府", "台中市政府"),
        "location:tc-city-council": ("臺中市議會", "台中市議會"),
        "location:tc-railway-station": ("臺中車站", "台中火車站"),
        "location:tc-national-theater": ("臺中國家歌劇院", "台中國家歌劇院"),
        "location:tc-rainbow-village": ("彩虹眷村", None),
        "location:tc-intercontinental-baseball": ("臺中洲際棒球場", "台中洲際棒球場"),
        "location:tc-wuri-fishing-port": ("梧棲漁港", None),
    }
    SHIPPED_DUPLICATE_CANDIDATES = {
        ("location", "中正路"): ["location:nt-zhongzheng-road", "location:tc-zhongzheng-road"],
        ("location", "中山路"): ["location:tc-zhongshan-road", "location:tn-zhongshan-road"],
        ("named_event", "臺中市政府跨年晚會"): [
            "named_event:tc-new-year-eve-2026",
            "named_event:tc-new-year-eve-2027",
        ],
        ("named_event", "臺中市議會定期會"): [
            "named_event:tc-council-regular-2026q4",
            "named_event:tc-council-regular-2027q1",
        ],
    }
    SEEDED_FIXTURE_ENTITY_IDS = (
        "location:tc-zhongzheng-road",
        "location:nt-zhongzheng-road",
        "location:tc-zhongshan-road",
        "location:tn-zhongshan-road",
        "location:tc-city-hall",
        "location:tc-city-council",
        "location:tc-railway-station",
        "location:tc-national-theater",
        "location:tc-rainbow-village",
        "location:tc-intercontinental-baseball",
        "location:tc-wuri-fishing-port",
        "named_event:tc-new-year-eve-2026",
        "named_event:tc-new-year-eve-2027",
        "named_event:tc-council-regular-2026q4",
        "named_event:tc-council-regular-2027q1",
    )
    NAME_DERIVATION_EVIDENCE = {"administrative-name", "official-road-name"}

    def test_shipped_registry_resolves_core_agencies_from_label_and_alias(self):
        """Pins #31 criterion 1: the core agency fixture set resolves as claimed.

        Most of these rows predate the change; the discriminating check is the
        council label, which also exists as a location row and must not cross
        kinds.
        """
        shipped = {entity["entity_id"] for entity in self.registry["entities"]}
        for entity_id, names in self.CORE_AGENCIES.items():
            self.assertIn(entity_id, shipped, entity_id)
            for name in names:
                resolved = er.resolve(self.registry, "agency", name, "臺中市")
                self.assertEqual(resolved["status"], "RESOLVED", name)
                self.assertEqual(resolved["entity_id"], entity_id, name)
        council_agency = er.resolve(self.registry, "agency", "臺中市議會", "臺中市")
        council_venue = er.resolve(self.registry, "location", "臺中市議會", "臺中市")
        self.assertEqual(council_agency["status"], "RESOLVED", "agency 臺中市議會")
        self.assertEqual(council_venue["status"], "RESOLVED", "location 臺中市議會")
        self.assertEqual(council_agency["entity_id"], "agency:tc-council")
        self.assertEqual(council_venue["entity_id"], "location:tc-city-council")

    def test_shipped_named_event_registry_resolves_one_label_per_date(self):
        rows = [entity for entity in self.registry["entities"] if entity["kind"] == "named_event"]
        self.assertTrue(rows, "shipped registry must ship NamedEvent rows")
        for (label, day), entity_id in self.NAMED_EVENTS.items():
            resolved = er.resolve(self.registry, "named_event", label, "臺中市", day)
            self.assertEqual(resolved["status"], "RESOLVED", (label, day))
            self.assertEqual(resolved["entity_id"], entity_id, (label, day))
            self.assertEqual(resolved["event_date"], day)

    def test_shipped_same_name_events_never_share_identity_across_dates(self):
        for label, days in self.SAME_NAME_EVENT_DATES.items():
            resolved_by_date = {}
            for day in days:
                resolved = er.resolve(self.registry, "named_event", label, "臺中市", day)
                self.assertEqual(resolved["status"], "RESOLVED", (label, day))
                resolved_by_date[day] = resolved["entity_id"]
            self.assertEqual(len(set(resolved_by_date.values())), 2, label)
            undated = er.resolve(self.registry, "named_event", label, "臺中市")
            self.assertEqual(undated["status"], "AMBIGUOUS", label)
            self.assertEqual(undated["candidate_ids"], sorted(resolved_by_date.values()), label)
            unlisted = er.resolve(self.registry, "named_event", label, "臺中市", "2028-01-01")
            self.assertEqual(unlisted["status"], "NO_MATCH", label)

    def test_shipped_same_name_roads_never_merge_across_cities(self):
        for name, (city, city_id, other, other_id) in self.SAME_NAME_ROADS.items():
            ambiguous = er.resolve(self.registry, "location", name)
            self.assertEqual(ambiguous["status"], "AMBIGUOUS", name)
            self.assertEqual(ambiguous["candidate_ids"], sorted([city_id, other_id]), name)
            scoped_city = er.resolve(self.registry, "location", name, city)
            scoped_other = er.resolve(self.registry, "location", name, other)
            self.assertEqual(scoped_city["status"], "RESOLVED", (name, city))
            self.assertEqual(scoped_other["status"], "RESOLVED", (name, other))
            self.assertEqual(scoped_city["entity_id"], city_id, (name, city))
            self.assertEqual(scoped_other["entity_id"], other_id, (name, other))
            self.assertNotEqual(
                scoped_city["entity_id"], scoped_other["entity_id"], name
            )

    def test_shipped_venue_and_district_fixtures_resolve_after_normalization(self):
        for entity_id, names in self.VENUES.items():
            for name in names:
                if name is None:
                    continue
                resolved = er.resolve(self.registry, "location", name, "臺中市")
                self.assertEqual(resolved["status"], "RESOLVED", name)
                self.assertEqual(resolved["entity_id"], entity_id, name)
        for name in ("台中市西屯區", "臺中市西屯區", "西屯"):
            resolved = er.resolve(self.registry, "location", name, "臺中市")
            self.assertEqual(resolved["status"], "RESOLVED", name)
            self.assertEqual(resolved["entity_id"], "location:tc-xitun", name)

    def test_shipped_duplicate_names_stay_ambiguous_and_carry_registry_receipt(self):
        by_name: dict[tuple[str, str], set[str]] = {}
        for (kind, _jurisdiction, _day, name), entity in er.build_index(self.registry).items():
            by_name.setdefault((kind, name), set()).add(entity["entity_id"])
        duplicated = {key for key, ids in by_name.items() if len(ids) > 1}
        self.assertTrue(duplicated, "shipped fixtures must exercise the duplicate-name guard")
        for key, expected_candidates in self.SHIPPED_DUPLICATE_CANDIDATES.items():
            self.assertIn(key, duplicated, key)
            self.assertEqual(sorted(by_name[key]), expected_candidates, key)
            result = er.resolve(self.registry, key[0], key[1])
            self.assertEqual(result["status"], "AMBIGUOUS", key)
            self.assertEqual(result["candidate_ids"], expected_candidates, key)
            self.assertEqual(result["registry_version"], self.registry["registry_version"], key)
            self.assertEqual(result["registry_hash"], er.registry_hash(self.registry), key)

    def test_default_registry_excludes_synthetic_event_dates_and_seeded_venues(self):
        canonical_ids = {row["entity_id"] for row in er.load_registry()["entities"]}
        self.assertFalse(canonical_ids & set(self.SEEDED_FIXTURE_ENTITY_IDS))
        for (label, day), entity_id in self.NAMED_EVENTS.items():
            self.assertEqual(er.resolve(er.load_registry(), "named_event", label, "臺中市", day)["status"], "NO_MATCH")

    def test_shipped_seeded_rows_declare_fixture_provenance(self):
        """A seeded row must never impersonate an audited official source."""
        catalog_ids = {
            row["source_id"]
            for row in json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))["sources"]
        }
        rows = {entity["entity_id"]: entity for entity in self.registry["entities"]}
        for entity_id in self.SEEDED_FIXTURE_ENTITY_IDS:
            self.assertIn(entity_id, rows, entity_id)
            evidence = rows[entity_id].get("evidence", "")
            self.assertTrue(evidence.startswith("fixture:"), (entity_id, evidence))
            self.assertNotIn("official-source-catalog", evidence, entity_id)
        for entity in self.registry["entities"]:
            if entity["entity_id"] in self.SEEDED_FIXTURE_ENTITY_IDS:
                continue
            evidence = entity.get("evidence", "")
            cited = evidence.removeprefix("official-source-catalog:")
            self.assertTrue(
                cited in catalog_ids or evidence in self.NAME_DERIVATION_EVIDENCE,
                (entity["entity_id"], evidence),
            )


class EntityRegistryRedirectChainTests(unittest.TestCase):
    """Inert, operator-approved fixture mappings; no production registry writes."""

    @staticmethod
    def registry():
        return {
            "schema_version": 1, "registry_version": 7, "updated_at": "2026-09-21",
            "redirects": {}, "entities": [
                {"entity_id": "agency:" + key, "kind": "agency",
                 "canonical_label": name + "局", "aliases": [name], "jurisdiction": "臺中市",
                 "status": "CONFIRMED", "evidence": "fixture:approved-" + key,
                 "alias_evidence": [{"alias": name, "evidence": "fixture:approved-" + key}]}
                for key, name in (("a", "甲"), ("b", "乙"), ("c", "丙"), ("d", "丁"))
            ],
        }

    @staticmethod
    def merge(registry, target, source, **kwargs):
        approved = {"evidence": "fixture:operator-reviewed-merge", "operator": "fixture-reviewer",
                    "decided_at": "2026-09-21T10:00:00+08:00"}
        approved.update(kwargs)
        return er.merge_entities(registry, target, source, **approved)

    @staticmethod
    def revert(registry, sequence):
        return er.revert_manual_change(registry, sequence, evidence="fixture:operator-reviewed-revert",
                                      operator="fixture-reviewer", decided_at="2026-09-21T10:01:00+08:00")

    def test_chained_merge_preserves_historical_ids_alias_evidence_and_audit(self):
        original = self.registry()
        original_bytes = json.dumps(original, ensure_ascii=False, sort_keys=True)
        first = self.merge(original, "agency:a", "agency:b")
        changed = self.merge(first, "agency:c", "agency:a")
        er.validate_registry(changed)
        self.assertEqual(er.resolve_entity_id(changed, "agency:b")["redirect_entity_ids"], ["agency:c"])
        self.assertEqual(er.resolve_entity_id(changed, "agency:a")["redirect_entity_ids"], ["agency:c"])
        self.assertEqual(er.resolve(changed, "agency", "乙局", "臺中市")["entity_id"], "agency:c")
        self.assertEqual(er.resolve(changed, "agency", "agency:b")["status"], "NO_MATCH")
        target = next(row for row in changed["entities"] if row["entity_id"] == "agency:c")
        self.assertEqual({row["evidence"] for row in target["alias_evidence"]},
                         {"fixture:approved-a", "fixture:approved-b", "fixture:approved-c"})
        self.assertEqual(changed["registry_version"], original["registry_version"] + 2)
        self.assertEqual([row["sequence"] for row in changed["audit_history"]], [1, 2])
        self.assertEqual([row["action"] for row in changed["audit_history"]], ["MERGE", "MERGE"])
        self.assertEqual(changed["audit_history"][1]["payload"]["before"]["incoming_redirects"], {"agency:b": ["agency:a"]})
        self.assertEqual(changed["audit_history"][1]["payload"]["after"]["incoming_redirects"], {"agency:b": ["agency:c"]})
        self.assertEqual(json.dumps(original, ensure_ascii=False, sort_keys=True), original_bytes)

    def test_chained_merge_reverts_in_reverse_order_without_losing_history(self):
        original = self.registry()
        first = self.merge(original, "agency:a", "agency:b")
        changed = self.merge(first, "agency:c", "agency:a")
        restored_first = self.revert(changed, 2)
        self.assertEqual(restored_first["redirects"], first["redirects"])
        self.assertEqual(sorted(restored_first["entities"], key=lambda row: row["entity_id"]),
                         sorted(first["entities"], key=lambda row: row["entity_id"]))
        restored = self.revert(restored_first, 1)
        self.assertEqual(restored["redirects"], original["redirects"])
        self.assertEqual(sorted(restored["entities"], key=lambda row: row["entity_id"]), original["entities"])
        self.assertEqual(restored["registry_version"], original["registry_version"] + 4)
        self.assertEqual([row["action"] for row in restored["audit_history"]], ["MERGE", "MERGE", "REVERT", "REVERT"])
        self.assertEqual(changed["redirects"], {"agency:b": ["agency:c"], "agency:a": ["agency:c"]})
        er.validate_registry(restored)

    def test_merging_split_children_deduplicates_then_restores_redirect_targets(self):
        original = {"schema_version": 1, "registry_version": 1, "redirects": {}, "entities": [
            {"entity_id": "location:old", "kind": "location", "canonical_label": "甲路",
             "aliases": ["乙路", "丙路"], "jurisdiction": "臺中市", "status": "CONFIRMED",
             "evidence": "fixture:approved-road", "alias_evidence": [
                 {"alias": "乙路", "evidence": "fixture:approved-alias-b"},
                 {"alias": "丙路", "evidence": "fixture:approved-alias-c"}]}]}
        first = er.split_entity(original, "location:old", [
            {"entity_id": "location:a", "canonical_label": "甲路", "aliases": ["乙路"]},
            {"entity_id": "location:c", "canonical_label": "丙路", "aliases": []}],
            evidence="fixture:operator-reviewed-split", operator="fixture-reviewer",
            decided_at="2026-09-21T10:00:00+08:00")
        changed = self.merge(first, "location:c", "location:a")
        self.assertEqual(changed["redirects"], {"location:old": ["location:c"], "location:a": ["location:c"]})
        restored_first = self.revert(changed, 2)
        self.assertEqual(restored_first["redirects"], {"location:old": ["location:a", "location:c"]})
        restored = self.revert(restored_first, 1)
        self.assertEqual(restored["entities"], original["entities"])
        self.assertEqual(restored["redirects"], {})
        self.assertEqual([row["action"] for row in restored["audit_history"]], ["SPLIT", "MERGE", "REVERT", "REVERT"])
        er.validate_registry(restored)

    def test_revert_refuses_a_changed_incoming_redirect(self):
        first = self.merge(self.registry(), "agency:a", "agency:b")
        changed = self.merge(first, "agency:c", "agency:a")
        changed["redirects"]["agency:b"] = ["agency:d"]
        er.validate_registry(changed)
        before = json.dumps(changed, ensure_ascii=False, sort_keys=True)
        with self.assertRaisesRegex(ValueError, "no longer matches merge incoming redirects"):
            self.revert(changed, 2)
        self.assertEqual(json.dumps(changed, ensure_ascii=False, sort_keys=True), before)

    def test_one_hop_merge_keeps_legacy_audit_shape_and_revert(self):
        original = self.registry()
        changed = self.merge(original, "agency:a", "agency:b")
        payload = changed["audit_history"][0]["payload"]
        self.assertEqual(set(payload["before"]), {"target", "source"})
        self.assertEqual(set(payload["after"]), {"target", "retired_entity", "redirect_entity_ids"})
        restored = self.revert(changed, 1)
        self.assertEqual(sorted(restored["entities"], key=lambda row: row["entity_id"]), original["entities"])
        self.assertEqual(restored["redirects"], {})

    def test_chained_merge_still_requires_manual_operator_evidence_and_time(self):
        first = self.merge(self.registry(), "agency:a", "agency:b")
        before = json.dumps(first, ensure_ascii=False, sort_keys=True)
        for kwargs, message in (({"operator": ""}, "operator is required"),
                                ({"evidence": ""}, "manual evidence is required"),
                                ({"decided_at": "2026-09-21T10:00:00"}, "timezone-aware")):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, message):
                    self.merge(first, "agency:c", "agency:a", **kwargs)
                self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
