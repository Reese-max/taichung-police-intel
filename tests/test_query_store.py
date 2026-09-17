from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import dataclass
from enum import Enum
from typing import Any

from intel_v2.query_store.index import IndexEntry, Generation, GenerationStatus, TrustTier
from intel_v2.query_store.schema import QueryStoreSchema, SCHEMA_VERSION
from intel_v2.query_store.store import QueryStore
from intel_v2.query_store.trust import classify_trust_tier


def make_canonical_publication(items: list[dict[str, Any]], *, hash_override: str | None = None) -> dict[str, Any]:
    entry_hashes: list[str] = []
    for item in items:
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        entry_hashes.append(hashlib.sha256(raw).hexdigest())
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "publication_id": "PUB-TEST-001",
        "items": items,
        "entry_hashes": entry_hashes,
    }
    if hash_override:
        body["publication_hash"] = hash_override
    else:
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        body["publication_hash"] = hashlib.sha256(raw).hexdigest()
    return body


def make_item(
    *,
    canonical_id: str,
    source_id: str = "S-009",
    title: str = "測試事件",
    region: str = "臺中市",
    agency: str = "警察局",
    category: str = "治安",
    status: str = "ACTIVE",
    trust_tier: TrustTier = TrustTier.VERIFIED,
    evidence_ids: list[str] | None = None,
    publication_hash: str = "pub-hash-001",
    source_locator: str = "S-009:doc-001:v1",
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "canonical_id": canonical_id,
        "source_id": source_id,
        "title": title,
        "region": region,
        "agency": agency,
        "category": category,
        "status": status,
        "trust_tier": trust_tier.value,
        "evidence_ids": evidence_ids or [],
        "publication_hash": publication_hash,
        "source_locator": source_locator,
        "detected_at": "2026-09-15T10:00:00+08:00",
    }
    entry.update(extra)
    return entry


class QueryStoreSchemaTest(unittest.TestCase):
    def test_schema_version_is_exposed(self):
        self.assertIsInstance(SCHEMA_VERSION, int)
        self.assertGreater(SCHEMA_VERSION, 0)

    def test_schema_snapshots_version(self):
        schema = QueryStoreSchema()
        self.assertEqual(schema.version, SCHEMA_VERSION)

    def test_schema_is_versioned_and_immutable(self):
        schema = QueryStoreSchema()
        with self.assertRaises(AttributeError):
            schema.version = 99


class TrustTierTest(unittest.TestCase):
    def test_trust_tier_values(self):
        values = {t.value for t in TrustTier}
        self.assertEqual(values, {"VERIFIED", "DISCOVERY_UNVERIFIED", "CONFLICT", "STALE"})


class ClassifyTrustTierTest(unittest.TestCase):
    def test_verified_for_canonical_event_with_evidence(self):
        tier = classify_trust_tier(
            source_id="S-009",
            event_type="EVENT",
            has_evidence=True,
            has_canonical_id=True,
            is_discovery=False,
        )
        self.assertEqual(tier, TrustTier.VERIFIED)

    def test_discovery_unverified_for_external_feed(self):
        tier = classify_trust_tier(
            source_id="DISCOVERY-FEED",
            event_type="EVENT",
            has_evidence=False,
            has_canonical_id=False,
            is_discovery=True,
        )
        self.assertEqual(tier, TrustTier.DISCOVERY_UNVERIFIED)

    def test_conflict_for_mismatched_sources(self):
        tier = classify_trust_tier(
            source_id="S-009",
            event_type="EVENT",
            has_evidence=True,
            has_canonical_id=True,
            is_discovery=False,
            conflicting=True,
        )
        self.assertEqual(tier, TrustTier.CONFLICT)

    def test_stale_for_missing_evidence(self):
        tier = classify_trust_tier(
            source_id="S-009",
            event_type="EVENT",
            has_evidence=False,
            has_canonical_id=True,
            is_discovery=False,
        )
        self.assertEqual(tier, TrustTier.STALE)


class IndexEntryTest(unittest.TestCase):
    def test_index_entry_has_canonical_traceability(self):
        entry = IndexEntry(
            canonical_id="PUB-EVT-001",
            publication_hash="pub-hash-001",
            source_locator="S-009:doc-001:v1",
            source_id="S-009",
            title="測試事件",
            region="臺中市",
            agency="警察局",
            category="治安",
            status="ACTIVE",
            trust_tier=TrustTier.VERIFIED,
            evidence_ids=["EVID-001"],
            detected_at="2026-09-15T10:00:00+08:00",
        )
        self.assertEqual(entry.canonical_id, "PUB-EVT-001")
        self.assertEqual(entry.publication_hash, "pub-hash-001")
        self.assertEqual(entry.source_locator, "S-009:doc-001:v1")
        self.assertEqual(entry.trust_tier, TrustTier.VERIFIED)

    def test_index_entry_serializes_to_dict(self):
        entry = IndexEntry(
            canonical_id="PUB-EVT-001",
            publication_hash="pub-hash-001",
            source_locator="S-009:doc-001:v1",
            source_id="S-009",
            title="測試事件",
            region="臺中市",
            agency="警察局",
            category="治安",
            status="ACTIVE",
            trust_tier=TrustTier.VERIFIED,
            evidence_ids=["EVID-001"],
            detected_at="2026-09-15T10:00:00+08:00",
        )
        d = entry.to_dict()
        self.assertEqual(d["canonical_id"], "PUB-EVT-001")
        self.assertIn("publication_hash", d)
        self.assertIn("source_locator", d)


class GenerationTest(unittest.TestCase):
    def test_generation_tracks_publication_hash(self):
        gen = Generation(
            generation_id="GEN-001",
            publication_hash="abc123",
            entries=[],
        )
        self.assertEqual(gen.generation_id, "GEN-001")
        self.assertEqual(gen.publication_hash, "abc123")

    def test_generation_status_defaults_to_active(self):
        gen = Generation(generation_id="GEN-001", publication_hash="abc123", entries=[])
        self.assertEqual(gen.status, GenerationStatus.ACTIVE)

    def test_generation_can_be_marked_stale(self):
        gen = Generation(generation_id="GEN-001", publication_hash="abc123", entries=[])
        gen.mark_stale("test degradation")
        self.assertEqual(gen.status, GenerationStatus.STALE)


class QueryStoreRebuildTest(unittest.TestCase):
    def setUp(self):
        self.store = QueryStore()

    def test_rebuild_from_canonical_publication(self):
        items = [
            make_item(canonical_id="EVT-A", source_id="S-009", title="事件A"),
            make_item(canonical_id="EVT-B", source_id="S-004", title="議事B"),
        ]
        publication = make_canonical_publication(items)
        generation = self.store.rebuild(publication)
        self.assertEqual(len(generation.entries), 2)
        self.assertEqual(generation.publication_hash, publication["publication_hash"])

    def test_rebuild_deterministic_ids_and_order(self):
        items = [
            make_item(canonical_id=f"EVT-{i:03d}", source_id="S-009", title=f"事件{i}")
            for i in range(5)
        ]
        publication = make_canonical_publication(items)
        gen1 = self.store.rebuild(publication)
        gen2 = self.store.rebuild(publication)
        ids1 = [e.canonical_id for e in gen1.entries]
        ids2 = [e.canonical_id for e in gen2.entries]
        self.assertEqual(ids1, ids2)

    def test_rebuild_every_entry_has_canonical_traceability(self):
        items = [
            make_item(
                canonical_id="EVT-001",
                source_id="S-009",
                evidence_ids=["EVID-001"],
                source_locator="S-009:doc-001:v1",
            ),
            make_item(
                canonical_id="EVT-002",
                source_id="S-004",
                evidence_ids=["EVID-002"],
                source_locator="S-004:doc-002:v1",
            ),
        ]
        publication = make_canonical_publication(items)
        generation = self.store.rebuild(publication)
        for entry in generation.entries:
            self.assertIsNotNone(entry.canonical_id)
            self.assertEqual(entry.publication_hash, publication["publication_hash"])
            self.assertIsNotNone(entry.source_locator)
            self.assertIn(":", entry.source_locator)

    def test_rebuild_classifies_trust_tiers(self):
        items = [
            make_item(canonical_id="EVT-VERIFIED", trust_tier=TrustTier.VERIFIED),
            make_item(canonical_id="EVT-DISCOVERY", trust_tier=TrustTier.DISCOVERY_UNVERIFIED),
            make_item(canonical_id="EVT-CONFLICT", trust_tier=TrustTier.CONFLICT),
            make_item(canonical_id="EVT-STALE", trust_tier=TrustTier.STALE),
        ]
        publication = make_canonical_publication(items)
        generation = self.store.rebuild(publication)
        tiers = {e.trust_tier for e in generation.entries}
        self.assertIn(TrustTier.VERIFIED, tiers)
        self.assertIn(TrustTier.DISCOVERY_UNVERIFIED, tiers)
        self.assertIn(TrustTier.CONFLICT, tiers)
        self.assertIn(TrustTier.STALE, tiers)


class QueryStoreSwapTest(unittest.TestCase):
    def setUp(self):
        self.store = QueryStore()

    def test_swap_generation_updates_active(self):
        items = [make_item(canonical_id="EVT-001", source_id="S-009")]
        publication = make_canonical_publication(items)
        gen = self.store.rebuild(publication)
        self.store.swap_generation(gen)
        self.assertEqual(self.store.get_active_generation(), gen)

    def test_swap_rejects_write_truth_mutation(self):
        items = [make_item(canonical_id="EVT-001", source_id="S-009")]
        publication = make_canonical_publication(items)
        gen = self.store.rebuild(publication)
        self.store.swap_generation(gen)
        with self.assertRaisesRegex(ValueError, "write truth|rebuild|swap"):
            self.store.rebuild(publication)

    def test_rebuild_preserves_previous_generation_on_failure(self):
        items_a = [make_item(canonical_id="EVT-A", source_id="S-009")]
        pub_a = make_canonical_publication(items_a)
        gen_a = self.store.rebuild(pub_a)
        self.store.swap_generation(gen_a)
        self.assertEqual(self.store.get_active_generation(), gen_a)
        self.assertEqual(gen_a.status, GenerationStatus.ACTIVE)

    def test_failed_rebuild_keeps_previous_active(self):
        items_a = [make_item(canonical_id="EVT-A", source_id="S-009")]
        pub_a = make_canonical_publication(items_a)
        gen_a = self.store.rebuild(pub_a)
        self.store.swap_generation(gen_a)
        self.assertEqual(self.store.get_active_generation(), gen_a)

        bad_publication = {"schema_version": 99, "items": []}
        with self.assertRaisesRegex(ValueError, "write truth|rebuild|swap"):
            self.store.rebuild(bad_publication)
        self.assertEqual(self.store.get_active_generation(), gen_a)
        self.assertEqual(gen_a.status, GenerationStatus.ACTIVE)

    def test_hash_gated_generation_switch(self):
        items = [make_item(canonical_id="EVT-001", source_id="S-009")]
        publication = make_canonical_publication(items)
        store1 = QueryStore()
        gen1 = store1.rebuild(publication)
        store1.swap_generation(gen1)

        store2 = QueryStore()
        gen2 = store2.rebuild(publication)
        self.assertEqual(gen1.publication_hash, gen2.publication_hash)

    def test_same_hash_marks_previous_stale(self):
        store = QueryStore()
        items = [make_item(canonical_id="EVT-001", source_id="S-009")]
        publication = make_canonical_publication(items)
        gen1 = store.rebuild(publication)
        store.swap_generation(gen1)
        self.assertEqual(gen1.status, GenerationStatus.ACTIVE)

        gen2 = Generation(
            generation_id="GEN-REPLACE",
            publication_hash=publication["publication_hash"],
            entries=gen1.entries,
        )
        store.swap_generation(gen2)
        self.assertEqual(gen1.status, GenerationStatus.STALE)
        self.assertEqual(gen2.status, GenerationStatus.ACTIVE)
        self.assertEqual(store.get_active_generation(), gen2)


class QueryStoreQueryTest(unittest.TestCase):
    def setUp(self):
        self.store = QueryStore()

    def _build_and_swap(self, items: list[dict[str, Any]]) -> Generation:
        publication = make_canonical_publication(items)
        gen = self.store.rebuild(publication)
        self.store.swap_generation(gen)
        return gen

    def test_query_with_pagination(self):
        items = [
            make_item(canonical_id=f"EVT-{i:03d}", source_id="S-009", title=f"事件{i:03d}")
            for i in range(20)
        ]
        self._build_and_swap(items)

        page1 = self.store.query(filters={}, limit=5, offset=0)
        page2 = self.store.query(filters={}, limit=5, offset=5)

        self.assertEqual(len(page1["results"]), 5)
        self.assertEqual(len(page2["results"]), 5)
        self.assertNotEqual(
            [r["canonical_id"] for r in page1["results"]],
            [r["canonical_id"] for r in page2["results"]],
        )
        self.assertEqual(page1["total"], 20)
        self.assertEqual(page2["total"], 20)
        self.assertIn("truncated", page1)
        self.assertIn("truncation_receipt", page1)

    def test_query_with_result_cap(self):
        items = [
            make_item(canonical_id=f"EVT-{i:03d}", source_id="S-009", title=f"事件{i:03d}")
            for i in range(20)
        ]
        self._build_and_swap(items)

        result = self.store.query(filters={}, limit=5)
        self.assertEqual(len(result["results"]), 5)
        self.assertTrue(result["truncated"])

    def test_query_filter_by_source_id(self):
        items = [
            make_item(canonical_id="EVT-S009", source_id="S-009", title="事件A"),
            make_item(canonical_id="EVT-S004", source_id="S-004", title="事件B"),
            make_item(canonical_id="EVT-S009-2", source_id="S-009", title="事件C"),
        ]
        self._build_and_swap(items)

        result = self.store.query(filters={"source_id": "S-009"})
        ids = {r["canonical_id"] for r in result["results"]}
        self.assertIn("EVT-S009", ids)
        self.assertIn("EVT-S009-2", ids)
        self.assertNotIn("EVT-S004", ids)

    def test_query_filter_by_trust_tier(self):
        items = [
            make_item(canonical_id="EVT-V", trust_tier=TrustTier.VERIFIED),
            make_item(canonical_id="EVT-D", trust_tier=TrustTier.DISCOVERY_UNVERIFIED),
            make_item(canonical_id="EVT-C", trust_tier=TrustTier.CONFLICT),
            make_item(canonical_id="EVT-S", trust_tier=TrustTier.STALE),
        ]
        self._build_and_swap(items)

        verified = self.store.query(filters={"trust_tier": "VERIFIED"})
        self.assertEqual(len(verified["results"]), 1)
        self.assertEqual(verified["results"][0]["canonical_id"], "EVT-V")

        conflict = self.store.query(filters={"trust_tier": "CONFLICT"})
        self.assertEqual(len(conflict["results"]), 1)

    def test_query_filter_by_status(self):
        items = [
            make_item(canonical_id="EVT-A", status="ACTIVE"),
            make_item(canonical_id="EVT-I", status="RESOLVED"),
        ]
        self._build_and_swap(items)

        active = self.store.query(filters={"status": "ACTIVE"})
        self.assertEqual(len(active["results"]), 1)
        self.assertEqual(active["results"][0]["canonical_id"], "EVT-A")

    def test_query_filter_by_region(self):
        items = [
            make_item(canonical_id="EVT-1", region="臺中市"),
            make_item(canonical_id="EVT-2", region="臺北市"),
        ]
        self._build_and_swap(items)

        result = self.store.query(filters={"region": "臺中市"})
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["canonical_id"], "EVT-1")

    def test_query_filter_by_agency(self):
        items = [
            make_item(canonical_id="EVT-1", agency="警察局"),
            make_item(canonical_id="EVT-2", agency="消防局"),
        ]
        self._build_and_swap(items)

        result = self.store.query(filters={"agency": "警察局"})
        self.assertEqual(len(result["results"]), 1)

    def test_query_filter_by_category(self):
        items = [
            make_item(canonical_id="EVT-1", category="治安"),
            make_item(canonical_id="EVT-2", category="交通"),
        ]
        self._build_and_swap(items)

        result = self.store.query(filters={"category": "治安"})
        self.assertEqual(len(result["results"]), 1)

    def test_query_full_text_search(self):
        items = [
            make_item(canonical_id="EVT-MATCH", source_id="S-009", title="霧峰分局整建"),
            make_item(canonical_id="EVT-NO", source_id="S-009", title="交通工程"),
        ]
        self._build_and_swap(items)

        result = self.store.query(filters={"q": "霧峰"})
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["canonical_id"], "EVT-MATCH")

    def test_query_empty_result(self):
        items = [make_item(canonical_id="EVT-001", source_id="S-009")]
        self._build_and_swap(items)

        result = self.store.query(filters={"source_id": "NONEXISTENT"})
        self.assertEqual(len(result["results"]), 0)
        self.assertEqual(result["total"], 0)

    def test_query_sort_is_deterministic(self):
        items = [
            make_item(canonical_id=f"EVT-{i:03d}", source_id="S-009", title=f"事件{i:03d}")
            for i in range(10)
        ]
        self._build_and_swap(items)

        result1 = self.store.query(filters={}, limit=100)
        result2 = self.store.query(filters={}, limit=100)
        ids1 = [r["canonical_id"] for r in result1["results"]]
        ids2 = [r["canonical_id"] for r in result2["results"]]
        self.assertEqual(ids1, ids2)


class QueryStoreWriteProtectionTest(unittest.TestCase):
    def setUp(self):
        self.store = QueryStore()

    def test_direct_write_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "write truth|rebuild|swap"):
            self.store._write_entry(canonical_id="EVT-001", data={})


class QueryStoreFallbackTest(unittest.TestCase):
    def setUp(self):
        self.store = QueryStore()

    def test_static_fallback_when_no_generation(self):
        result = self.store.query_or_static(filters={}, limit=10)
        self.assertIsNotNone(result)
        self.assertIn("results", result)


class QueryStoreIntegrationTest(unittest.TestCase):
    def test_full_rebuild_and_query_cycle(self):
        store = QueryStore()
        items = [
            make_item(
                canonical_id="EVT-001",
                source_id="S-009",
                title="建請重新整建臺中市政府警察局霧峰分局案",
                agency="警察局",
                category="治安",
                region="臺中市",
                status="ACTIVE",
                trust_tier=TrustTier.VERIFIED,
                evidence_ids=["EVID-001", "EVID-002"],
                source_locator="S-009:proposal-001:v1",
            ),
            make_item(
                canonical_id="EVT-002",
                source_id="S-004",
                title="第四屆第8次定期會議事日程表",
                agency="議會",
                category="議事",
                region="臺中市",
                status="ACTIVE",
                trust_tier=TrustTier.VERIFIED,
                evidence_ids=["EVID-003"],
                source_locator="S-004:agenda-001:v1",
            ),
            make_item(
                canonical_id="EVT-003",
                source_id="DISCOVERY-FEED",
                title="外部事件發現層候選",
                agency="Discovery",
                category="Discovery",
                region="臺中市",
                status="PENDING",
                trust_tier=TrustTier.DISCOVERY_UNVERIFIED,
                evidence_ids=[],
                source_locator="DISCOVERY:candidate-001",
            ),
        ]
        publication = make_canonical_publication(items)

        gen1 = store.rebuild(publication)
        store.swap_generation(gen1)

        self.assertEqual(store.get_active_generation(), gen1)

        all_results = store.query(filters={}, limit=100)
        self.assertEqual(all_results["total"], 3)

        s009_results = store.query(filters={"source_id": "S-009"})
        self.assertEqual(len(s009_results["results"]), 1)
        self.assertEqual(s009_results["results"][0]["canonical_id"], "EVT-001")

        verified_results = store.query(filters={"trust_tier": "VERIFIED"})
        self.assertEqual(len(verified_results["results"]), 2)

        page = store.query(filters={}, limit=2, offset=0)
        self.assertEqual(len(page["results"]), 2)
        self.assertEqual(page["total"], 3)
        self.assertTrue(page["truncated"])

        page2 = store.query(filters={}, limit=2, offset=2)
        self.assertEqual(len(page2["results"]), 1)

    def test_deterministic_rebuild_two_times(self):
        store = QueryStore()
        items = [
            make_item(canonical_id=f"EVT-{i:03d}", source_id="S-009", title=f"事件{i}")
            for i in range(7)
        ]
        publication = make_canonical_publication(items)

        gen_a = store.rebuild(publication)
        ids_a = [(e.canonical_id, e.publication_hash) for e in gen_a.entries]

        gen_b = store.rebuild(publication)
        ids_b = [(e.canonical_id, e.publication_hash) for e in gen_b.entries]

        self.assertEqual(ids_a, ids_b)

    def test_stale_generation_falls_back_to_static(self):
        store = QueryStore()
        items = [make_item(canonical_id="EVT-001", source_id="S-009")]
        publication = make_canonical_publication(items)
        gen = store.rebuild(publication)
        store.swap_generation(gen)
        gen.mark_stale("rebuild failed")

        result = store.query(filters={}, limit=10)
        self.assertEqual(result.get("fallback"), "static_govintel")
        self.assertEqual(result["total"], 0)

    def test_filter_consistency_returns_same_canonical_ids(self):
        store = QueryStore()
        items = [
            make_item(canonical_id="EVT-X", source_id="S-009", region="臺中市", agency="警察局", title="獨立事件"),
            make_item(canonical_id="EVT-Y", source_id="S-004", region="臺北市", agency="議會", title="另一事件"),
        ]
        publication = make_canonical_publication(items)
        gen = store.rebuild(publication)
        store.swap_generation(gen)

        by_id = store.query(filters={"canonical_id": "EVT-X"})
        by_source = store.query(filters={"source_id": "S-009"})

        ids_by_id = {r["canonical_id"] for r in by_id["results"]}
        ids_by_source = {r["canonical_id"] for r in by_source["results"]}
        self.assertIn("EVT-X", ids_by_id)
        self.assertIn("EVT-X", ids_by_source)
        self.assertNotIn("EVT-Y", ids_by_id)


class QueryStorePublicIntegrationTest(unittest.TestCase):
    def test_rebuild_from_intelligence_feed_shape(self):
        feed_items = [
            {
                "canonical_id": "EVT-FEED-1",
                "source_id": "S-009",
                "title": "建議警察局加強宣導",
                "region": "臺中市",
                "agency": "警察局",
                "category": "治安",
                "status": "ACTIVE",
                "trust_tier": "VERIFIED",
                "evidence_ids": ["EVID-100"],
                "source_locator": "S-009:proposal-100:v1",
                "detected_at": "2026-09-15T10:00:00+08:00",
            }
        ]
        store = QueryStore()
        publication = make_canonical_publication(feed_items)
        gen = store.rebuild(publication)
        self.assertEqual(len(gen.entries), 1)
        entry = gen.entries[0]
        self.assertEqual(entry.trust_tier, TrustTier.VERIFIED)
        self.assertEqual(entry.source_id, "S-009")
        store.swap_generation(gen)

        result = store.query(filters={})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["canonical_id"], "EVT-FEED-1")
