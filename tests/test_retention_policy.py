import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "retention-policy.py"
spec = importlib.util.spec_from_file_location("retention_policy", SCRIPT)
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RetentionPolicyTests(unittest.TestCase):
    def test_catalog_has_one_conservative_policy_per_source(self):
        compiled = retention.compile_policy()
        self.assertEqual(set(compiled["source_policies"]), set(compiled["source_classes"]))
        self.assertTrue(all(value["public_projection"] in retention.PUBLIC_PROJECTIONS for value in compiled["source_policies"].values()))
        self.assertTrue(all(not value["full_text_allowed"] for value in compiled["source_policies"].values()))

    def test_policy_compile_is_deterministic_and_hash_bound(self):
        first = retention.compile_policy()
        second = retention.compile_policy()
        self.assertEqual(first, second)
        self.assertRegex(first["policy_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(first["catalog_hash"], r"^[0-9a-f]{64}$")

    def test_unknown_rights_cannot_become_unreviewed_full_text(self):
        policy = retention.load_json(retention.POLICY)
        mutated = copy.deepcopy(policy)
        mutated["classes"]["OFFICIAL_METADATA_LINK"]["review_required"] = False
        with self.assertRaisesRegex(ValueError, "unknown rights"):
            retention.compile_policy(policy=mutated)

    def test_prohibited_public_field_cannot_become_allowlisted(self):
        policy = retention.load_json(retention.POLICY)
        mutated = copy.deepcopy(policy)
        mutated["classes"]["OFFICIAL_METADATA_LINK"]["public_fields"] = ["raw_payload"]
        with self.assertRaisesRegex(ValueError, "prohibited"):
            retention.compile_policy(policy=mutated)

    def test_media_source_is_link_only_and_dry_run_preserves_audit_linkage(self):
        compiled = retention.compile_policy()
        self.assertEqual(compiled["source_policies"]["S-010"]["public_projection"], "LINK_ONLY")
        receipt = retention.plan_expiry([
            {
                "record_id": "raw-media-1",
                "source_id": "S-010",
                "layer": "raw_snapshot",
                "captured_at": "2026-09-01T00:00:00+00:00",
                "content_sha256": "a" * 64,
                "audit_refs": ["AUDIT-1"],
            },
            {
                "record_id": "event-1",
                "source_id": "S-010",
                "layer": "canonical_event",
                "captured_at": "2026-01-01T00:00:00+00:00",
                "content_sha256": "b" * 64,
                "audit_refs": ["AUDIT-2"],
            },
        ], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertEqual(receipt["counts"], {"total": 2, "retained": 1, "expired": 1, "blocked": 0, "replay_limited": 0})
        self.assertEqual(receipt["records"][0]["action"], "PURGE_RAW_KEEP_AUDIT")
        self.assertEqual(receipt["records"][1]["action"], "RETAIN")
        self.assertTrue(receipt["dry_run"])
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_transient_fire_source_uses_short_raw_window(self):
        compiled = retention.compile_policy()
        self.assertEqual(compiled["source_policies"]["S-031"]["raw_retention_class"], "SHORT_LIVED_SOURCE_TERMS_REVIEW")
        receipt = retention.plan_expiry([
            {
                "record_id": "fire-raw-1",
                "source_id": "S-031",
                "layer": "raw_snapshot",
                "captured_at": "2026-09-01T00:00:00+00:00",
                "content_sha256": "d" * 64,
                "audit_refs": ["AUDIT-FIRE-1"],
            },
            {
                "record_id": "fire-event-1",
                "source_id": "S-031",
                "layer": "canonical_event",
                "captured_at": "2026-01-01T00:00:00+00:00",
                "content_sha256": "e" * 64,
                "audit_refs": ["AUDIT-FIRE-2"],
            },
        ], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertEqual(receipt["records"][0]["action"], "PURGE_RAW_KEEP_AUDIT")
        self.assertEqual(receipt["records"][1]["action"], "RETAIN")

    def test_expired_raw_without_audit_linkage_is_blocked_and_private_data_fails_closed(self):
        base = {
            "record_id": "raw-media-2",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "content_sha256": "c" * 64,
            "audit_refs": [],
        }
        receipt = retention.plan_expiry([base], observed_at="2026-09-21T00:00:00+00:00")
        self.assertEqual(receipt["records"][0]["status"], "BLOCKED")
        private = dict(base, private_notes="do not publish")
        with self.assertRaisesRegex(ValueError, "prohibited"):
            retention.plan_expiry([private], observed_at="2026-09-21T00:00:00+00:00")

    def test_expired_canonical_without_audit_linkage_is_blocked(self):
        compiled = copy.deepcopy(retention.compile_policy())
        compiled["retention_windows"]["PROVENANCE_HISTORY"] = {
            "max_age_days": 7,
            "expired_action": "KEEP_AUDIT_LINKAGE",
        }
        receipt = retention.plan_expiry([{
            "record_id": "event-without-audit",
            "source_id": "S-010",
            "layer": "canonical_event",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "f" * 64,
            "audit_refs": [],
        }], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertEqual(receipt["records"][0]["status"], "BLOCKED")
        self.assertEqual(receipt["records"][0]["action"], "BLOCKED_NO_AUDIT_LINKAGE")

    def test_versioned_matrix_covers_every_declared_data_type_and_layer(self):
        compiled = retention.compile_policy()
        self.assertEqual(compiled["policy_version"], 2)
        matrix = compiled["data_types"]
        self.assertEqual(
            set(matrix),
            {
                "OFFICIAL_OPEN_DATA_STRUCTURED_RECORD",
                "OFFICIAL_HTML_PDF_ATTACHMENT",
                "OFFICIAL_TRANSIENT_LIVE_ALERT",
                "MEDIA_RSS_NEWS_DISCOVERY_METADATA",
                "LLM_DERIVED_SUMMARY_LABEL",
                "RAW_SNAPSHOT",
                "NORMALIZED_DOCUMENT",
                "CANONICAL_GOVINTEL_EVENT",
                "CANONICAL_GOVINTEL_RECEIPT",
                "QUERY_INDEX",
            },
        )
        self.assertEqual(set(compiled["layer_policies"]), retention.RETENTION_LAYERS)
        self.assertEqual(matrix["RAW_SNAPSHOT"]["retention_class_source"], "raw_retention_class")
        self.assertEqual(matrix["QUERY_INDEX"]["retention_class_source"], "canonical_retention_class")
        self.assertEqual(matrix["RAW_SNAPSHOT"]["archive_eligibility"], "NOT_ARCHIVABLE")
        self.assertEqual(matrix["QUERY_INDEX"]["public_projection"], "METADATA_LINK_ONLY")
        content = {name for name, entry in matrix.items() if entry["kind"] == "CONTENT"}
        layers = {name for name, entry in matrix.items() if entry["kind"] == "LAYER"}
        self.assertEqual(content | layers, set(matrix))
        for name in content:
            self.assertIn(matrix[name]["class_id"], compiled["classes"])
        for name in layers:
            self.assertIn(matrix[name]["layer"], compiled["layer_policies"])

    def test_matrix_structure_fails_closed_when_a_layer_or_data_type_is_missing(self):
        policy = retention.load_json(retention.POLICY)
        incomplete = copy.deepcopy(policy)
        del incomplete["layer_policies"]["query_index"]
        with self.assertRaisesRegex(ValueError, "every retention layer"):
            retention.compile_policy(policy=incomplete)
        extra = copy.deepcopy(policy)
        extra["layer_policies"]["shadow_index"] = dict(extra["layer_policies"]["query_index"])
        with self.assertRaisesRegex(ValueError, "every retention layer"):
            retention.compile_policy(policy=extra)
        missing_type = copy.deepcopy(policy)
        missing_type["data_type_matrix"] = [
            entry for entry in missing_type["data_type_matrix"]
            if entry["data_type"] != "QUERY_INDEX"
        ]
        with self.assertRaisesRegex(ValueError, "every retention layer must be governed"):
            retention.compile_policy(policy=missing_type)
        unknown_class = copy.deepcopy(policy)
        unknown_class["data_type_matrix"][0]["class_id"] = "NOT_A_CLASS"
        with self.assertRaisesRegex(ValueError, "unknown retention class"):
            retention.compile_policy(policy=unknown_class)
        undated = copy.deepcopy(policy)
        undated["classes"]["DERIVED_SUMMARY"]["withdrawal_projection"] = "SOURCE_NO_LONGER_AVAILABLE"
        with self.assertRaisesRegex(ValueError, "withdrawal projection must match withdrawal behavior"):
            retention.compile_policy(policy=undated)
        untyped = copy.deepcopy(policy)
        untyped["rights_status_values"].pop("UNKNOWN")
        with self.assertRaisesRegex(ValueError, "rights status is undocumented"):
            retention.compile_policy(policy=untyped)
        project_owned = copy.deepcopy(policy)
        project_owned["classes"]["OFFICIAL_MEDIA_LINK"]["terms_status"] = "PROJECT_OWNED_NO_THIRD_PARTY_LICENSE"
        with self.assertRaisesRegex(ValueError, "open permission"):
            retention.compile_policy(policy=project_owned)
        unflagged = copy.deepcopy(policy)
        unflagged["classes"]["REFERENCE_METADATA"]["sensitive_default"] = "yes"
        with self.assertRaisesRegex(ValueError, "sensitive_default must be a boolean"):
            retention.compile_policy(policy=unflagged)
        undeclared = copy.deepcopy(policy)
        undeclared["replay_limitation_reasons"] = {"SOME_OTHER_REASON": "unrelated limitation"}
        with self.assertRaisesRegex(ValueError, "withdrawn-source reason"):
            retention.compile_policy(policy=undeclared)

    def test_archive_gate_blocks_any_class_marked_never_archivable(self):
        policy = retention.load_json(retention.POLICY)
        policy["classes"]["OFFICIAL_MEDIA_LINK"]["archive_eligibility"] = "NOT_ARCHIVABLE"
        compiled = retention.compile_policy(policy=policy)
        decision = retention.archive_decision({
            "record_id": "media-1",
            "source_id": "S-010",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
        }, policy=compiled)
        self.assertEqual(decision["decision"], "BLOCKED")
        self.assertEqual(decision["archive_eligibility"], "NOT_ARCHIVABLE")
        with self.assertRaisesRegex(ValueError, "archive"):
            retention.project_public_record({
                "record_id": "media-1",
                "source_id": "S-010",
                "layer": "canonical_event",
                "captured_at": "2026-09-01T00:00:00+00:00",
            }, policy=compiled)

    def test_catalog_terms_reference_must_be_a_https_entrypoint(self):
        catalog = retention.load_json(retention.CATALOG)
        catalog["sources"][0]["entrypoint"] = "http://insecure.example.test/feed"
        with self.assertRaisesRegex(ValueError, "terms reference"):
            retention.compile_policy(catalog=catalog)
        catalog = retention.load_json(retention.CATALOG)
        del catalog["sources"][0]["entrypoint"]
        with self.assertRaisesRegex(ValueError, "terms reference"):
            retention.compile_policy(catalog=catalog)

    def test_every_catalog_source_publishes_terms_archive_and_sensitive_facts(self):
        compiled = retention.compile_policy()
        catalog = retention.load_json(retention.CATALOG)
        entrypoints = {row["source_id"]: row["entrypoint"] for row in catalog["sources"]}
        for source_id, source_policy in compiled["source_policies"].items():
            self.assertTrue(source_policy["terms_url"].startswith("https://"), source_id)
            self.assertEqual(source_policy["terms_url"], entrypoints[source_id])
            self.assertIn(source_policy["archive_eligibility"], retention.ARCHIVE_ELIGIBILITY)
            self.assertIn(source_policy["withdrawal_projection"], retention.WITHDRAWAL_PROJECTIONS)
            self.assertIsInstance(source_policy["sensitive_default"], bool)
            self.assertTrue(source_policy["data_types"])

    def test_unknown_rights_cannot_be_declared_an_open_license(self):
        compiled = retention.compile_policy()
        for source_id, source_policy in compiled["source_policies"].items():
            if source_policy["rights_status"] != "UNKNOWN":
                continue
            self.assertTrue(source_policy["review_required"], source_id)
            self.assertEqual(source_policy["terms_status"], "CATALOG_ENTRYPOINT_NOT_A_LICENSE", source_id)
        policy = retention.load_json(retention.POLICY)
        mutated = copy.deepcopy(policy)
        mutated["classes"]["OFFICIAL_MEDIA_LINK"]["terms_status"] = "VERIFIED_OPEN_PERMISSION"
        with self.assertRaisesRegex(ValueError, "open permission"):
            retention.compile_policy(policy=mutated)
        mutated = copy.deepcopy(policy)
        mutated["classes"]["OFFICIAL_MEDIA_LINK"]["rights_status"] = "VERIFIED_OPEN_PERMISSION"
        with self.assertRaisesRegex(ValueError, "rights status"):
            retention.compile_policy(policy=mutated)

    def test_media_review_required_projects_link_only_and_never_full_text(self):
        projection = retention.project_public_record({
            "record_id": "media-1",
            "source_id": "S-010",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "title": "議會質詢影音",
            "official_url": "https://www.tccc.gov.tw/",
            "full_text": "long transcript body",
            "excerpt": "quote",
            "body": "html",
        })
        self.assertEqual(projection["public_projection"], "LINK_ONLY")
        self.assertTrue(projection["review_required"])
        self.assertEqual(projection["rights_status"], "UNKNOWN")
        self.assertEqual(projection["projected_fields"], {
            "title": "議會質詢影音",
            "official_url": "https://www.tccc.gov.tw/",
        })
        self.assertEqual(projection["dropped_fields"], ["body", "excerpt", "full_text"])
        self.assertNotIn("full_text", projection["projected_fields"])
        self.assertNotIn("long transcript body", json.dumps(projection, ensure_ascii=False))

    def test_withdrawn_official_page_keeps_history_and_marks_source_no_longer_available(self):
        projection = retention.project_public_record({
            "record_id": "notice-1",
            "source_id": "S-001",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "title": "警政新聞",
            "official_url": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
            "content_sha256": "a" * 64,
            "published_at": "2026-08-30T00:00:00+00:00",
            "source_available": False,
        })
        self.assertEqual(projection["source_availability"], "SOURCE_NO_LONGER_AVAILABLE")
        self.assertFalse(projection["currently_available"])
        self.assertEqual(projection["projected_fields"]["content_sha256"], "a" * 64)
        self.assertEqual(projection["projected_fields"]["official_url"],
                         "https://www.police.taichung.gov.tw/ch/home.jsp?id=1")

    def test_retracted_projection_keeps_only_provenance(self):
        projection = retention.project_public_record({
            "record_id": "summary-1",
            "source_id": "S-001",
            "class_id": "DERIVED_SUMMARY",
            "layer": "publication",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "title": "derived title",
            "claim_id": "CLAIM-1",
            "source_available": False,
        })
        self.assertEqual(projection["withdrawal_projection"], "RETRACTED")
        self.assertEqual(projection["source_availability"], "RETRACTED")
        self.assertEqual(projection["projected_fields"], {})

    def test_query_index_rebuild_cannot_resurrect_expired_prohibited_full_text(self):
        compiled = retention.compile_policy()
        index = retention.project_query_index([{
            "record_id": "feed-1",
            "source_id": "S-010",
            "layer": "query_index",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "title": "質詢影音",
            "official_url": "https://www.tccc.gov.tw/",
            "full_text": "expired official body",
            "raw_payload": {"html": "<html>"},
            "private_notes": "internal",
        }], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertTrue(index["projection_only"])
        self.assertFalse(index["extends_source_retention"])
        entry = index["entries"][0]
        self.assertTrue(entry["source_raw_retention_expired"])
        self.assertNotIn("full_text", entry["projected_fields"])
        self.assertNotIn("raw_payload", entry["projected_fields"])
        self.assertEqual(entry["suppressed_fields"], ["full_text", "private_notes", "raw_payload"])
        self.assertIn("full_text", entry["suppressed_fields"])
        self.assertEqual(entry["archive_eligibility"], "PROVENANCE_ONLY")
        self.assertEqual(entry["retention_class"], "PROVENANCE_HISTORY")
        self.assertEqual(entry["source_raw_retention_class"], "SHORT_LIVED_SOURCE_TERMS_REVIEW")
        self.assertRegex(index["index_hash"], r"^[0-9a-f]{64}$")
        self.assertNotIn("expired official body", json.dumps(index, ensure_ascii=False))

    def test_personal_data_fixture_fails_closed_for_archive_and_publication(self):
        record = {
            "record_id": "case-1",
            "source_id": "S-028",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "official_url": "https://data.gov.tw/dataset/88147",
        }
        decision = retention.archive_decision(record)
        self.assertEqual(decision["decision"], "BLOCKED")
        self.assertIn("aggregate_only", decision["reason"])

        flagged = dict(record, contains_personal_data=True)
        self.assertEqual(retention.archive_decision(flagged)["decision"], "BLOCKED")
        sensitive = dict(record, sensitive=True)
        self.assertEqual(retention.archive_decision(sensitive)["decision"], "BLOCKED")
        self.assertEqual(retention.archive_decision(dict(record, aggregate_only=True))["decision"], "ARCHIVE")

        with self.assertRaisesRegex(ValueError, "archive"):
            retention.project_public_record(flagged)
        with self.assertRaisesRegex(ValueError, "archive"):
            retention.project_public_record(sensitive)
        with self.assertRaisesRegex(ValueError, "prohibited"):
            retention.project_public_record(dict(record, personal_data={"name": "redacted"}))
        with self.assertRaisesRegex(ValueError, "prohibited"):
            retention.plan_expiry([dict(record, raw_payload={"html": "<html>"})],
                                  observed_at="2026-09-21T00:00:00+00:00")

        dropped = retention.project_public_record(dict(record, aggregate_only=True, raw_payload={"html": "<html>"}))
        self.assertEqual(dropped["dropped_fields"], ["raw_payload"])
        self.assertNotIn("raw_payload", json.dumps(dropped["projected_fields"], ensure_ascii=False))

    def test_transient_source_expiry_keeps_audit_receipt_and_never_revives_raw(self):
        compiled = retention.compile_policy()
        receipt = retention.plan_expiry([{
            "record_id": "fire-raw",
            "source_id": "S-031",
            "layer": "raw_snapshot",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-FIRE-RAW"],
        }], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        row = receipt["records"][0]
        self.assertEqual(row["status"], "EXPIRED")
        self.assertEqual(row["action"], "PURGE_RAW_KEEP_AUDIT")
        self.assertEqual(row["audit_refs"], ["AUDIT-FIRE-RAW"])

    def test_replay_required_evidence_is_preserved_or_explicitly_limited(self):
        compiled = retention.compile_policy()
        record = {
            "record_id": "replay-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-REPLAY-1"],
            "replay_required": True,
        }
        receipt = retention.plan_expiry([record], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        row = receipt["records"][0]
        self.assertEqual(row["action"], "KEEP_AUDIT_LINKAGE")
        self.assertEqual(row["replay_status"], "PRESERVED")
        self.assertIsNone(row["replay_limitation"])
        self.assertEqual(receipt["counts"]["replay_limited"], 0)

        limited = retention.plan_expiry(
            [dict(record, source_available=False)],
            observed_at="2026-09-21T00:00:00+00:00",
            policy=compiled,
        )["records"][0]
        self.assertEqual(limited["replay_status"], "LIMITED")
        self.assertEqual(limited["replay_limitation"], "SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL")
        self.assertEqual(limited["action"], "KEEP_AUDIT_LINKAGE")

        blocked = retention.plan_expiry(
            [dict(record, audit_refs=[])],
            observed_at="2026-09-21T00:00:00+00:00",
            policy=compiled,
        )["records"][0]
        self.assertEqual(blocked["replay_status"], "BLOCKED")
        self.assertEqual(blocked["action"], "BLOCKED_NO_AUDIT_LINKAGE")

        untouched = retention.plan_expiry(
            [dict(record, captured_at="2026-09-20T00:00:00+00:00")],
            observed_at="2026-09-21T00:00:00+00:00",
            policy=compiled,
        )["records"][0]
        self.assertEqual(untouched["replay_status"], "PRESERVED")
        self.assertEqual(untouched["action"], "RETAIN")

        plain = retention.plan_expiry([
            {
                "record_id": "plain-raw",
                "source_id": "S-010",
                "layer": "raw_snapshot",
                "captured_at": "2026-01-01T00:00:00+00:00",
                "content_sha256": "b" * 64,
                "audit_refs": ["AUDIT-PLAIN"],
            }
        ], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)["records"][0]
        self.assertEqual(plain["action"], "PURGE_RAW_KEEP_AUDIT")
        self.assertEqual(plain["replay_status"], "NOT_REQUIRED")


if __name__ == "__main__":
    unittest.main()
