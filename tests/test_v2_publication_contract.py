import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-v2-publication.py"
SPEC = importlib.util.spec_from_file_location("verify_v2_publication", SCRIPT)
VERIFY = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(VERIFY)


def event(**overrides):
    value = {
        "event_id": "E-1",
        "identity": "S-009:proposal-1",
        "watch_id": "WATCH-1",
        "stable_key": "proposal-1",
        "source_id": "S-009",
        "source_name": "各項提案",
        "change_type": "REVISED",
        "headline": "提案更新",
        "what_changed": "官方內容更新",
        "why_it_matters": "需要確認政策影響。",
        "affected_roles": ["局本部幕僚"],
        "recommended_action": "開啟官方來源核對。",
        "deadline": None,
        "temporal_basis": "DETECTED_CHANGE",
        "date_status": "UNVERIFIED_DATE",
        "detected_at": "2026-09-21T09:00:00+08:00",
        "changed_fields": ["title"],
        "source_version": 2,
        "source_sha256": "a" * 64,
        "source_document_version": "doc-v2",
        "official_url": "https://example.gov.tw/proposal-1",
        "verification_status": "DETERMINISTIC_PASS",
        "evidence_status": "OFFICIAL_URL_BOUND",
    }
    value.update(overrides)
    return value


def view(item):
    return {"priority_items": [item], "other_changes": []}


class V2PublicationContractTests(unittest.TestCase):
    def test_profile_views_share_canonical_event_fields(self):
        VERIFY.validate_profile_view_consistency([view(event()), view(event())])
        with self.assertRaisesRegex(ValueError, "canonical event differs"):
            VERIFY.validate_profile_view_consistency([view(event()), view(event(what_changed="被 profile 改寫"))])


if __name__ == "__main__":
    unittest.main()
