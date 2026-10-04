"""Regression checks for the Query Domain's static public replay projection."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("query_replay_builder", ROOT / "scripts/build-public-query-replay.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def fixture():
    payload = {"road": "測試路 A 至 B", "district": "西屯區（合成）", "status": "IN_PROGRESS",
               "effective_from": "2026-10-05T09:00:00+08:00", "effective_to": "2026-10-07T17:00:00+08:00",
               "daily": {"start": "09:00", "end": "17:00"}, "text": "【合成測試】測試路 A 至 B 施工"}
    updates = []
    for update_id, acquired_at, end in [("UPD-001", "2026-10-05T09:20:00+08:00", "2026-10-07T17:00:00+08:00"),
                                        ("UPD-005", "2026-10-08T08:40:00+08:00", "2026-10-09T17:00:00+08:00")]:
        fields = deepcopy(payload)
        fields["effective_to"] = end
        updates.append({"update_id": update_id, "acquired_at": acquired_at, "published_at": acquired_at,
                        "source_id": "S-SYN-ROAD", "document_id": "DOC-ROAD-AB", "origin": "OFFICIAL",
                        "rights": "SYNTHETIC_NO_REAL_ROAD", "payload": fields, "content_sha256": builder.query_domain.digest(fields)})
    return {"schema_version": 1, "synthetic": True, "scenario_id": "test-saved-query", "notice": "合成示例",
            "data_cutoff": "2026-10-08T18:30:00+08:00", "updates": updates,
            "reopen_points": [{"reopen_id": "R3", "reopened_at": "2026-10-08T08:50:00+08:00", "note": "延期"}],
            "collections": [{"source_id": "S-SYN-ROAD", "observed_at": "2026-10-08T08:50:00+08:00",
                             "snapshot_complete": True, "visible_update_ids": ["UPD-001", "UPD-005"]}]}


class PublicQueryInterfaceTests(unittest.TestCase):
    def test_projection_uses_domain_comparison_and_real_digests(self):
        bundle = builder.build_replay(fixture())
        event = bundle["snapshots"]["R3"]["events"][0]
        self.assertEqual(event["comparison"]["changed_fields"], ["effective_to"])
        self.assertEqual(event["comparison"]["before"]["fields"]["daily"], "09:00–17:00")
        self.assertEqual(event["comparison"]["after"]["fields"]["effective_to"], "2026-10-09T17:00:00+08:00")
        self.assertEqual(event["documents"][0]["document_version_id"], "UPD-001")
        self.assertEqual(event["documents"][0]["evidence_locator"], "fixture:test-saved-query#UPD-001")
        material = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        self.assertEqual(bundle["bundle_sha256"], builder.query_domain.digest(material))
        self.assertEqual(builder.build_replay(fixture()), bundle)

    def test_mismatched_payload_or_unapproved_rights_fail_closed(self):
        forged = fixture()
        forged["updates"][0]["payload"]["effective_to"] = "2026-10-12T17:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            builder.build_replay(forged)
        wrong_rights = fixture()
        wrong_rights["updates"][0]["rights"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "rights"):
            builder.build_replay(wrong_rights)

    def test_replay_never_claims_production_or_fabricated_d1_d2(self):
        bundle = builder.build_replay(fixture())
        self.assertEqual(bundle["mode"], "SYNTHETIC_REPLAY")
        self.assertEqual(bundle["namespace"], "demo:commute")
        self.assertEqual(bundle["capabilities"], {"D1": "CAPABILITY_NOT_AVAILABLE", "D2": "CAPABILITY_NOT_AVAILABLE"})
        self.assertIn("synthetic.invalid", bundle["snapshots"]["R3"]["events"][0]["documents"][0]["official_url"])

    def test_saved_bundle_is_signed_and_each_event_keeps_exact_original_payload(self):
        bundle = json.loads((ROOT / "apps/web/public/data/public-query-replay.json").read_text(encoding="utf-8"))
        material = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        self.assertEqual(bundle["bundle_sha256"], builder.query_domain.digest(material))
        for snapshot in bundle["snapshots"].values():
            for event in snapshot["events"]:
                for original in event["payloads"]:
                    self.assertEqual(original["content_sha256"], builder.query_domain.digest(original["payload"]))


if __name__ == "__main__":
    unittest.main()
