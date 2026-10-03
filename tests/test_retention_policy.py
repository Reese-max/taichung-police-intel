import copy
from datetime import datetime, timezone
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
        catalog = retention.load_json(retention.CATALOG)
        compiled = retention.compile_policy()
        catalog_ids = {row["source_id"] for row in catalog["sources"]}
        self.assertEqual(set(compiled["source_policies"]), catalog_ids)
        self.assertEqual(set(compiled["source_classes"]), catalog_ids)
        # A source the policy forgot must fail closed, not silently drop out.
        catalog["sources"] = [row for row in catalog["sources"] if row["source_id"] != "S-010"]
        with self.assertRaisesRegex(ValueError, "every catalog source must have exactly one"):
            retention.compile_policy(catalog=catalog)
        for value in compiled["source_policies"].values():
            self.assertIn(value["public_projection"], retention.PUBLIC_PROJECTIONS)
            self.assertFalse(value["full_text_allowed"])
            self.assertEqual(value["public_fields"], compiled["classes"][value["class_id"]]["public_fields"])

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
        self.assertEqual(receipt["counts"], {
            "total": 2, "retained": 1, "expired": 1, "blocked": 0,
            "replay_preserved": 0, "replay_limited": 0, "replay_blocked": 0,
        })
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
        for name, entry in matrix.items():
            if entry["kind"] == "CONTENT":
                self.assertIn(entry["class_id"], compiled["classes"])
            else:
                self.assertIn(entry["layer"], compiled["layer_policies"])
        # Every class must be reachable through at least one content data type.
        covered = {entry["class_id"] for entry in matrix.values() if entry["kind"] == "CONTENT"}
        self.assertEqual(covered, set(compiled["classes"]))

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
        publishes_nothing = copy.deepcopy(policy)
        publishes_nothing["layer_policies"]["raw_snapshot"]["archive_eligibility"] = "EVIDENCE_ARCHIVE"
        with self.assertRaisesRegex(ValueError, "publishes nothing must not be archivable"):
            retention.compile_policy(policy=publishes_nothing)
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
        with self.assertRaisesRegex(ValueError, "every reason the planner can emit"):
            retention.compile_policy(policy=undeclared)
        # Each reason the planner can emit must be declared on its own, not just as a set.
        for reason in ("REPLAY_EVIDENCE_INCOMPLETE", "REPLAY_REVIEW_WINDOW_PENDING",
                       "SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL"):
            with self.subTest(missing_reason=reason):
                without = copy.deepcopy(policy)
                without["replay_limitation_reasons"] = {
                    name: text for name, text in without["replay_limitation_reasons"].items()
                    if name != reason
                }
                with self.assertRaisesRegex(ValueError, "every reason the planner can emit"):
                    retention.compile_policy(policy=without)

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
            "layer": "publication",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "title": "derived title",
            "claim_id": "CLAIM-1",
            "source_available": False,
        }, class_id="DERIVED_SUMMARY")
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
        }], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertTrue(index["projection_only"])
        self.assertFalse(index["extends_source_retention"])
        entry = index["entries"][0]
        self.assertTrue(entry["source_raw_retention_expired"])
        self.assertNotIn("full_text", entry["projected_fields"])
        self.assertNotIn("raw_payload", entry["projected_fields"])
        self.assertEqual(entry["suppressed_fields"], ["full_text", "raw_payload"])
        self.assertEqual(entry["archive_eligibility"], "PROVENANCE_ONLY")
        self.assertEqual(entry["retention_class"], "PROVENANCE_HISTORY")
        self.assertEqual(entry["source_raw_retention_class"], "SHORT_LIVED_SOURCE_TERMS_REVIEW")
        # The hashes must describe the payloads a consumer actually receives.
        self.assertEqual(entry["projection_hash"], retention.sha256(
            {key: value for key, value in entry.items() if key != "projection_hash"}))
        self.assertRegex(index["index_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(index["index_hash"], retention.sha256(
            {key: value for key, value in index.items() if key != "index_hash"}))
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

        # S-001 is not sensitive_default, so these flags are the only thing blocking them.
        news = {
            "record_id": "news-1",
            "source_id": "S-001",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "official_url": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
        }
        self.assertEqual(retention.archive_decision(news)["decision"], "ARCHIVE")
        for flag in ("contains_personal_data", "sensitive"):
            flagged = dict(news, **{flag: True})
            decision = retention.archive_decision(flagged)
            self.assertEqual(decision["decision"], "BLOCKED", flag)
            self.assertEqual(decision["reason"], f"{flag} is true")
            with self.assertRaisesRegex(ValueError, flag):
                retention.project_public_record(flagged)
            with self.assertRaisesRegex(ValueError, flag):
                retention.project_query_index(
                    [dict(flagged, captured_at="2026-09-01T00:00:00+00:00")],
                    observed_at="2026-09-21T00:00:00+00:00",
                )
        for marker in ("personal_data", "private_notes", "operational_fields", "credentials"):
            with self.assertRaisesRegex(ValueError, "prohibited"):
                retention.project_public_record(dict(news, **{marker: "leaked"}))
            with self.assertRaisesRegex(ValueError, "prohibited"):
                retention.project_query_index(
                    [dict(news, captured_at="2026-09-01T00:00:00+00:00", **{marker: "leaked"})],
                    observed_at="2026-09-21T00:00:00+00:00",
                )
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
        self.assertTrue(receipt["dry_run"])
        # The receipt hash must bind the receipt body, not just look like a hash.
        self.assertEqual(receipt["receipt_sha256"], retention.sha256(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}))
        tampered = copy.deepcopy(receipt)
        tampered["records"][0]["audit_refs"] = []
        self.assertNotEqual(receipt["receipt_sha256"], retention.sha256(
            {key: value for key, value in tampered.items() if key != "receipt_sha256"}))

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
            "source_snapshot_ref": "SNAP-1",
            "official_url": "https://www.tccc.gov.tw/",
            "locator": "#quality",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
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

    def test_raw_and_normalized_layers_are_never_publicly_archivable(self):
        for layer in ("raw_snapshot", "normalized_document"):
            record = {
                "record_id": f"{layer}-1",
                "source_id": "S-001",
                "layer": layer,
                "captured_at": "2026-09-01T00:00:00+00:00",
                "title": "警政新聞",
                "official_url": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
            }
            decision = retention.archive_decision(record)
            self.assertEqual(decision["decision"], "BLOCKED", layer)
            self.assertEqual(decision["blocking_archive_eligibility"], "NOT_ARCHIVABLE", layer)
            self.assertIn("never publicly archivable", decision["reason"])
            with self.assertRaisesRegex(ValueError, "never publicly archivable"):
                retention.project_public_record(record)
        published = retention.project_public_record({
            "record_id": "event-1",
            "source_id": "S-001",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "title": "警政新聞",
        })
        self.assertEqual(published["layer_archive_eligibility"], "EVIDENCE_ARCHIVE")
        self.assertEqual(published["layer_public_projection"], "METADATA_LINK_ONLY")
        self.assertEqual(published["projected_fields"]["title"], "警政新聞")

    def test_unsupported_layer_fails_closed_for_every_entry_point(self):
        record = {
            "record_id": "x-1",
            "source_id": "S-001",
            "layer": "shadow_index",
            "captured_at": "2026-09-01T00:00:00+00:00",
        }
        for call in (
            lambda: retention.archive_decision(record),
            lambda: retention.project_public_record(record),
            lambda: retention.project_query_index([record], observed_at="2026-09-21T00:00:00+00:00"),
            lambda: retention.plan_expiry([dict(record, content_sha256="a" * 64)],
                                          observed_at="2026-09-21T00:00:00+00:00"),
        ):
            with self.assertRaisesRegex(ValueError, "unsupported retention layer"):
                call()

    def test_a_stored_record_cannot_reclassify_itself_into_a_permissive_class(self):
        record = {
            "record_id": "case-1",
            "source_id": "S-028",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
        }
        self.assertEqual(retention.archive_decision(record)["decision"], "BLOCKED")
        # A class stored in the payload is ignored: only a trusted caller argument counts.
        self.assertEqual(
            retention.archive_decision(dict(record, class_id="DERIVED_SUMMARY"))["decision"],
            "BLOCKED",
        )
        self.assertEqual(
            retention.archive_decision(record, class_id="DERIVED_SUMMARY")["decision"],
            "ARCHIVE",
        )
        self.assertEqual(
            retention.archive_decision(record, class_id="REFERENCE_METADATA")["decision"],
            "BLOCKED",
        )
        with self.assertRaisesRegex(ValueError, "unknown retention class"):
            retention.archive_decision(record, class_id="NOT_A_CLASS")
        self.assertNotIn("class_id", retention.RECORD_CONTROL_FIELDS)
        overridden = retention.project_public_record(dict(record), class_id="DERIVED_SUMMARY")
        self.assertEqual(overridden["class_id"], "DERIVED_SUMMARY")
        self.assertEqual(overridden["rights_status"], "PROJECT_CONTROLLED")
        self.assertEqual(overridden["data_types"],
                         retention.compile_policy()["class_data_types"]["DERIVED_SUMMARY"])
        with self.assertRaisesRegex(ValueError, "sensitive by default"):
            retention.project_public_record(dict(record, class_id="DERIVED_SUMMARY"))
        aggregate = retention.project_public_record(dict(record, aggregate_only=True))
        self.assertEqual(aggregate["class_id"], "REFERENCE_METADATA")
        self.assertEqual(aggregate["rights_status"], "UNKNOWN")

    def test_non_serializable_projection_value_is_reported_not_crashed(self):
        with self.assertRaisesRegex(ValueError, "not JSON serializable"):
            retention.project_public_record({
                "record_id": "news-1",
                "source_id": "S-001",
                "layer": "canonical_event",
                "captured_at": "2026-09-01T00:00:00+00:00",
                "published_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            })

    def test_expiry_receipt_hash_binds_its_rows(self):
        compiled = retention.compile_policy()
        base = {
            "record_id": "news-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-1"],
        }
        first = retention.plan_expiry([base], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        second = retention.plan_expiry(
            [dict(base, content_sha256="b" * 64)],
            observed_at="2026-09-21T00:00:00+00:00",
            policy=compiled,
        )
        self.assertNotEqual(first["receipt_sha256"], second["receipt_sha256"])

    def test_replay_override_never_outranks_review_required_or_missing_linkage(self):
        policy = retention.load_json(retention.POLICY)
        policy["retention_windows"]["SHORT_LIVED_SOURCE_TERMS_REVIEW"] = {
            "max_age_days": 7,
            "expired_action": "REVIEW_REQUIRED",
        }
        compiled = retention.compile_policy(policy=policy)
        record = {
            "record_id": "replay-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-REPLAY-1"],
            "replay_required": True,
            "source_snapshot_ref": "SNAP-1",
            "official_url": "https://www.tccc.gov.tw/",
            "locator": "#quality",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        row = retention.plan_expiry([record], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)["records"][0]
        self.assertEqual(row["action"], "REVIEW_REQUIRED")
        self.assertIsNone(row["overridden_expired_action"])
        self.assertEqual(row["replay_missing_fields"], [])
        # Evidence is retained, but replay is pending that review, not preserved.
        self.assertEqual(row["replay_status"], "LIMITED")
        self.assertEqual(row["replay_limitation"], "REPLAY_REVIEW_WINDOW_PENDING")
        self.assertIn("REPLAY_REVIEW_WINDOW_PENDING", compiled["replay_limitation_reasons"])

        # A canonical layer must not downgrade the review window either.
        policy["retention_windows"]["PROVENANCE_HISTORY"] = {
            "max_age_days": 7,
            "expired_action": "REVIEW_REQUIRED",
        }
        canonical = retention.plan_expiry(
            [dict(record, layer="canonical_event")],
            observed_at="2026-09-21T00:00:00+00:00",
            policy=retention.compile_policy(policy=policy),
        )["records"][0]
        self.assertEqual(canonical["action"], "REVIEW_REQUIRED")
        self.assertEqual(canonical["replay_status"], "LIMITED")
        self.assertEqual(canonical["replay_limitation"], "REPLAY_REVIEW_WINDOW_PENDING")

        purged = retention.compile_policy()
        full = retention.plan_expiry([record], observed_at="2026-09-21T00:00:00+00:00", policy=purged)["records"][0]
        self.assertEqual(full["action"], "KEEP_AUDIT_LINKAGE")
        self.assertEqual(full["overridden_expired_action"], "PURGE_RAW_KEEP_AUDIT")

    def test_replay_without_audit_or_provenance_is_blocked_with_a_declared_limitation(self):
        compiled = retention.compile_policy()
        base = {
            "record_id": "replay-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "replay_required": True,
        }
        no_linkage = retention.plan_expiry(
            [dict(base, audit_refs=[])], observed_at="2026-09-21T00:00:00+00:00", policy=compiled,
        )["records"][0]
        self.assertEqual(no_linkage["replay_status"], "BLOCKED")
        self.assertEqual(no_linkage["replay_limitation"], "REPLAY_EVIDENCE_INCOMPLETE")
        self.assertNotEqual(no_linkage["action"], "KEEP_AUDIT_LINKAGE")

        no_provenance = retention.plan_expiry(
            [dict(base, audit_refs=["AUDIT-1"])],
            observed_at="2026-09-21T00:00:00+00:00",
            policy=compiled,
        )["records"][0]
        self.assertEqual(no_provenance["replay_status"], "BLOCKED")
        self.assertEqual(no_provenance["replay_limitation"], "REPLAY_EVIDENCE_INCOMPLETE")
        self.assertEqual(no_provenance["replay_missing_fields"],
                         ["created_at", "locator", "official_url", "source_snapshot_ref", "updated_at"])
        self.assertIn("REPLAY_EVIDENCE_INCOMPLETE", compiled["replay_limitation_reasons"])

    def test_receipt_counts_expose_every_replay_outcome(self):
        compiled = retention.compile_policy()
        record = {
            "record_id": "replay-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-REPLAY-1"],
            "replay_required": True,
            "source_snapshot_ref": "SNAP-1",
            "official_url": "https://www.tccc.gov.tw/",
            "locator": "#quality",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        receipt = retention.plan_expiry([
            record,
            dict(record, record_id="limited", source_available=False),
            dict(record, record_id="blocked", audit_refs=[]),
            {
                "record_id": "plain",
                "source_id": "S-010",
                "layer": "raw_snapshot",
                "captured_at": "2026-01-01T00:00:00+00:00",
                "content_sha256": "b" * 64,
                "audit_refs": ["AUDIT-PLAIN"],
            },
        ], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertEqual(receipt["counts"]["replay_preserved"], 1)
        self.assertEqual(receipt["counts"]["replay_limited"], 1)
        self.assertEqual(receipt["counts"]["replay_blocked"], 1)
        self.assertEqual(receipt["counts"]["total"], 4)
        self.assertEqual(receipt["replay_evidence_fields"], compiled["replay_evidence_fields"])
        for row in receipt["records"]:
            if row["replay_required"] and row["replay_status"] != "PRESERVED":
                self.assertIn(row["replay_limitation"], compiled["replay_limitation_reasons"], row["record_id"])
            if not row["replay_required"]:
                self.assertIsNone(row["replay_limitation"])

    def test_null_governance_flags_are_rejected_instead_of_read_as_false(self):
        for field in ("replay_required", "source_available", "contains_personal_data", "sensitive"):
            record = {
                "record_id": "x-1",
                "source_id": "S-001",
                "layer": "canonical_event",
                "captured_at": "2026-09-01T00:00:00+00:00",
                field: None,
            }
            with self.assertRaisesRegex(ValueError, f"{field} must be a boolean"):
                retention.plan_expiry([dict(record, content_sha256="a" * 64)],
                                      observed_at="2026-09-21T00:00:00+00:00")
            with self.assertRaisesRegex(ValueError, f"{field} must be a boolean"):
                retention.archive_decision(record)

    def test_class_vocabulary_is_exact_so_no_field_can_shadow_a_source_fact(self):
        policy = retention.load_json(retention.POLICY)
        extended = copy.deepcopy(policy)
        extended["classes"]["OFFICIAL_MEDIA_LINK"]["terms_url"] = "https://project-internal.example/policy"
        with self.assertRaisesRegex(ValueError, "do not match the compiled vocabulary"):
            retention.compile_policy(policy=extended)
        dropped = copy.deepcopy(policy)
        del dropped["classes"]["OFFICIAL_MEDIA_LINK"]["sensitive_default"]
        with self.assertRaisesRegex(ValueError, "do not match the compiled vocabulary"):
            retention.compile_policy(policy=dropped)

    def test_every_matrix_section_is_bound_into_the_policy_hash(self):
        baseline = retention.compile_policy()["policy_hash"]
        policy = retention.load_json(retention.POLICY)
        for section, mutate in (
            ("layer_policies", lambda value: value["layer_policies"].__setitem__(
                "query_index", dict(value["layer_policies"]["query_index"],
                                    archive_eligibility="EVIDENCE_ARCHIVE"))),
            ("data_type_matrix", lambda value: value["data_type_matrix"].append(
                {"data_type": "EXTRA", "kind": "CONTENT", "class_id": "DERIVED_SUMMARY"})),
            ("rights_status_values", lambda value: value["rights_status_values"].__setitem__(
                "UNKNOWN", "a different description for the same status")),
            ("terms_status_values", lambda value: value["terms_status_values"].__setitem__(
                "CATALOG_ENTRYPOINT_NOT_A_LICENSE", "a different description for the same status")),
            ("replay_evidence_fields", lambda value: value["replay_evidence_fields"].append("extra_field")),
        ):
            mutated = copy.deepcopy(policy)
            with self.subTest(section=section):
                mutate(mutated)
                self.assertNotEqual(retention.compile_policy(policy=mutated)["policy_hash"], baseline)
        for section in retention.REQUIRED_POLICY_SECTIONS:
            incomplete = copy.deepcopy(policy)
            del incomplete[section]
            with self.subTest(missing_section=section):
                with self.assertRaisesRegex(ValueError, "missing required sections"):
                    retention.compile_policy(policy=incomplete)

    def test_public_binding_is_derived_and_never_advertises_an_open_permission(self):
        compiled = retention.compile_policy()
        binding = retention.policy_binding(compiled)
        self.assertEqual(binding["policy_version"], compiled["policy_version"])
        self.assertEqual(binding["policy_hash"], compiled["policy_hash"])
        self.assertFalse(binding["full_text_allowed"])
        self.assertFalse(binding["excerpt_allowed"])
        self.assertFalse(binding["query_index_extends_source_retention"])
        self.assertTrue(binding["review_required"])
        # Conservative by construction: the strictest class wins, never the most permissive.
        self.assertEqual(binding["rights_status"], "UNKNOWN")
        self.assertEqual(binding["terms_status"], "CATALOG_ENTRYPOINT_NOT_A_LICENSE")
        self.assertIn("PROJECT_CONTROLLED", binding["rights_status_values"])
        for value in compiled["classes"].values():
            if value["rights_status"] == "UNKNOWN":
                self.assertEqual(value["terms_status"], "CATALOG_ENTRYPOINT_NOT_A_LICENSE")

    def test_checked_in_binding_matches_the_compiled_policy(self):
        compiled = retention.compile_policy()
        committed = json.loads(
            (ROOT / "apps" / "web" / "public" / "data" / "retention-policy-binding.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(committed, retention.policy_binding(compiled))

    def test_gateway_advertises_the_compiled_binding_not_a_hand_written_block(self):
        gateway = (ROOT / "scripts" / "query-gateway.py").read_text(encoding="utf-8")
        self.assertIn('"retention": dict(RETENTION_BINDING)', gateway)
        self.assertEqual(gateway.count('"retention": dict(RETENTION_BINDING)'), 3)
        self.assertEqual(gateway.count('"retention":'), 3)
        self.assertNotRegex(gateway, r'"retention":\s*\{')
        self.assertNotIn("RETENTION_POLICY = {", gateway)

    def test_query_index_gates_the_layer_it_writes_into_not_the_layer_claimed(self):
        policy = retention.load_json(retention.POLICY)
        policy["layer_policies"]["query_index"] = {
            "retention_class_source": "canonical_retention_class",
            "archive_eligibility": "NOT_ARCHIVABLE",
            "public_projection": "METADATA_LINK_ONLY",
        }
        compiled = retention.compile_policy(policy=policy)
        record = {
            "record_id": "news-1",
            "source_id": "S-001",
            "layer": "canonical_event",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "title": "警政新聞",
            "official_url": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
        }
        # Declaring query_index still hits the layer's own NOT_ARCHIVABLE gate.
        with self.assertRaisesRegex(ValueError, "never publicly archivable"):
            retention.project_query_index(
                [dict(record, layer="query_index")],
                observed_at="2026-09-21T00:00:00+00:00",
                policy=compiled,
            )
        with self.assertRaisesRegex(ValueError, "never publicly archivable"):
            retention.project_public_record(dict(record), policy=compiled, layer="query_index")
        # Under the shipped policy a canonical_event record is archivable but still does
        # not belong in this index.
        with self.assertRaisesRegex(ValueError, "unsupported retention layer for the query index"):
            retention.project_query_index(
                [record], observed_at="2026-09-21T00:00:00+00:00", policy=retention.compile_policy())
        # A layer-less record must be gated as a query index, not left unresolved. If the
        # gate trusted the record, this would raise "unsupported retention layer" instead.
        layerless = {key: value for key, value in record.items() if key != "layer"}
        with self.assertRaisesRegex(ValueError, "never publicly archivable"):
            retention.project_query_index(
                [layerless], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        # The same record is fine as a canonical event, so the block is the layer, not the data.
        canonical = retention.project_public_record(dict(record), policy=compiled, layer="canonical_event")
        self.assertEqual(canonical["projected_fields"]["title"], "警政新聞")

    def test_preserved_replay_can_never_carry_a_limitation(self):
        compiled = retention.compile_policy()
        record = {
            "record_id": "replay-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-REPLAY-1"],
            "replay_required": True,
            "source_snapshot_ref": "SNAP-1",
            "official_url": "https://www.tccc.gov.tw/",
            "locator": "#quality",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        for source_available, expected_status, expected_limitation in (
            (None, "PRESERVED", None),
            (True, "PRESERVED", None),
            (False, "LIMITED", "SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL"),
        ):
            row = dict(record)
            if source_available is None:
                row.pop("source_available", None)
            else:
                row["source_available"] = source_available
            planned = retention.plan_expiry(
                [row], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)["records"][0]
            self.assertEqual(planned["replay_status"], expected_status)
            self.assertEqual(planned["replay_limitation"], expected_limitation)
            self.assertEqual(planned["action"], "KEEP_AUDIT_LINKAGE")

    def test_binding_projection_follows_the_query_index_layer(self):
        policy = retention.load_json(retention.POLICY)
        policy["layer_policies"]["query_index"]["public_projection"] = "LINK_ONLY"
        compiled = retention.compile_policy(policy=policy)
        self.assertEqual(retention.policy_binding(compiled)["public_projection"], "LINK_ONLY")
        self.assertEqual(retention.compile_policy()["layer_policies"]["query_index"]["public_projection"],
                         "METADATA_LINK_ONLY")

    def test_query_index_layer_must_publish_something_the_binding_can_advertise(self):
        policy = retention.load_json(retention.POLICY)
        policy["layer_policies"]["query_index"] = {
            "retention_class_source": "canonical_retention_class",
            "archive_eligibility": "NOT_ARCHIVABLE",
            "public_projection": "NONE",
        }
        with self.assertRaisesRegex(ValueError, "query index layer must publish"):
            retention.compile_policy(policy=policy)

    def test_binding_aggregates_conservatively_and_binds_the_catalog(self):
        compiled = retention.compile_policy()
        binding = retention.policy_binding(compiled)
        self.assertEqual(binding["catalog_hash"], compiled["catalog_hash"])
        self.assertEqual(binding["public_projection"],
                         compiled["layer_policies"]["query_index"]["public_projection"])
        self.assertTrue(binding["review_required"])
        catalog = retention.load_json(retention.CATALOG)
        catalog["sources"][0]["entrypoint"] = "https://www.police.taichung.gov.tw/ch/moved.jsp"
        moved = retention.compile_policy(catalog=catalog)
        self.assertEqual(moved["policy_hash"], compiled["policy_hash"])
        self.assertNotEqual(moved["catalog_hash"], compiled["catalog_hash"])
        self.assertEqual(retention.policy_binding(moved)["catalog_hash"], moved["catalog_hash"])
        projection = retention.project_public_record({
            "record_id": "news-1",
            "source_id": "S-001",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
        }, policy=moved)
        self.assertEqual(projection["catalog_hash"], moved["catalog_hash"])
        self.assertEqual(projection["terms_url"], "https://www.police.taichung.gov.tw/ch/moved.jsp")

    def test_content_payload_fields_must_stay_prohibited(self):
        policy = retention.load_json(retention.POLICY)
        policy["prohibited_public_fields"] = [
            field for field in policy["prohibited_public_fields"] if field != "raw_payload"
        ]
        with self.assertRaisesRegex(ValueError, "content payload field must be prohibited"):
            retention.compile_policy(policy=policy)

    def test_malformed_policy_input_is_reported_as_a_value_error(self):
        policy = retention.load_json(retention.POLICY)
        for section in ("replay_evidence_fields", "prohibited_public_fields"):
            malformed = copy.deepcopy(policy)
            malformed[section] = [{}]
            with self.subTest(section=section):
                with self.assertRaises(ValueError):
                    retention.compile_policy(policy=malformed)
        nested_field = copy.deepcopy(policy)
        nested_field["classes"]["OFFICIAL_MEDIA_LINK"]["public_fields"] = [{"raw": "payload"}]
        with self.assertRaises(ValueError):
            retention.compile_policy(policy=nested_field)
        bad_catalog = retention.load_json(retention.CATALOG)
        bad_catalog["sources"].append("not-an-object")
        with self.assertRaisesRegex(ValueError, "every catalog source must be an object"):
            retention.compile_policy(catalog=bad_catalog)

    def test_null_governance_flags_are_rejected_including_aggregate_only(self):
        for field in ("aggregate_only", "replay_required", "source_available",
                      "contains_personal_data", "sensitive"):
            record = {
                "record_id": "news-1",
                "source_id": "S-001",
                "layer": "canonical_event",
                "captured_at": "2026-09-01T00:00:00+00:00",
                field: None,
            }
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    retention.archive_decision(record)
                with self.assertRaisesRegex(ValueError, field):
                    retention.plan_expiry([dict(record, content_sha256="a" * 64)],
                                          observed_at="2026-09-21T00:00:00+00:00")

    def test_compile_rejects_a_non_object_catalog_or_policy(self):
        for keyword, value in (("catalog", []), ("catalog", "oops"), ("policy", []), ("policy", 7)):
            with self.subTest(keyword=keyword, value=value):
                with self.assertRaisesRegex(ValueError, "must be an object"):
                    retention.compile_policy(**{keyword: value})

    def test_binding_review_required_quotes_the_strictest_class(self):
        policy = retention.load_json(retention.POLICY)
        policy["classes"]["DERIVED_SUMMARY"]["review_required"] = False
        compiled = retention.compile_policy(policy=policy)
        self.assertTrue(retention.policy_binding(compiled)["review_required"])
        for value in compiled["classes"].values():
            if value["rights_status"] == "UNKNOWN":
                self.assertTrue(value["review_required"])

    def test_query_index_projection_only_flag_cannot_disagree_across_payloads(self):
        compiled = retention.compile_policy()
        entry = retention.project_query_index([{
            "record_id": "feed-1",
            "source_id": "S-001",
            "layer": "query_index",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "title": "警政新聞",
        }], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertIs(entry["extends_source_retention"], retention.QUERY_INDEX_EXTENDS_SOURCE_RETENTION)
        self.assertIs(entry["entries"][0]["extends_source_retention"],
                      retention.QUERY_INDEX_EXTENDS_SOURCE_RETENTION)
        self.assertIs(retention.policy_binding(compiled)["query_index_extends_source_retention"],
                      retention.QUERY_INDEX_EXTENDS_SOURCE_RETENTION)

    def test_overridden_expired_action_is_only_set_when_the_action_changed(self):
        compiled = retention.compile_policy()
        record = {
            "record_id": "replay-raw",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "content_sha256": "a" * 64,
            "audit_refs": ["AUDIT-REPLAY-1"],
            "replay_required": True,
            "source_snapshot_ref": "SNAP-1",
            "official_url": "https://www.tccc.gov.tw/",
            "locator": "#quality",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        overridden = retention.plan_expiry(
            [record], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)["records"][0]
        self.assertEqual(overridden["action"], "KEEP_AUDIT_LINKAGE")
        self.assertEqual(overridden["overridden_expired_action"], "PURGE_RAW_KEEP_AUDIT")

        policy = retention.load_json(retention.POLICY)
        policy["retention_windows"]["SHORT_LIVED_SOURCE_TERMS_REVIEW"] = {
            "max_age_days": 7,
            "expired_action": "KEEP_AUDIT_LINKAGE",
        }
        same = retention.plan_expiry(
            [record], observed_at="2026-09-21T00:00:00+00:00",
            policy=retention.compile_policy(policy=policy),
        )["records"][0]
        self.assertEqual(same["action"], "KEEP_AUDIT_LINKAGE")
        self.assertIsNone(same["overridden_expired_action"])

    def test_flag_errors_never_echo_the_offending_value(self):
        record = {
            "record_id": "news-1",
            "source_id": "S-001",
            "layer": "canonical_event",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "contains_personal_data": "王小明 身分證 123456789",
        }
        with self.assertRaises(ValueError) as caught:
            retention.archive_decision(record)
        self.assertIn("contains_personal_data must be a boolean, not str", str(caught.exception))
        self.assertNotIn("王小明", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
