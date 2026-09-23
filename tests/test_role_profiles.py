from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path

from intel_v2.handoff import empty_state
from intel_v2.role_profiles import load_catalog, project_profile, rank_items, validate_catalog
from intel_v2.semantics import ChangeEvent


PROFILE_PATH = "docs/govintel/role-profiles.v1.json"
NOW = "2026-09-21T09:00:00+08:00"
BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-v2-shadow-brief.py"
BUILD_SPEC = importlib.util.spec_from_file_location("build_v2_shadow_brief", BUILD_SCRIPT)
BUILD = importlib.util.module_from_spec(BUILD_SPEC)
BUILD_SPEC.loader.exec_module(BUILD)


def event(
    event_id: str,
    *,
    source_id: str = "S-009",
    headline: str = "一般行政公告",
    roles: list[str] | None = None,
    deadline: str | None = None,
    change_type: str = "REVISED",
    changed_fields: list[str] | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "source_id": source_id,
        "source_name": source_id,
        "change_type": change_type,
        "headline": headline,
        "what_changed": headline,
        "why_it_matters": "公開政策資訊異動。",
        "recommended_action": "確認官方內容。",
        "affected_roles": roles or [],
        "deadline": deadline,
        "detected_at": NOW,
        "changed_fields": changed_fields or [],
        "official_url": f"https://example.gov.tw/{event_id}",
        "verification_status": "DETERMINISTIC_PASS",
        "evidence_status": "OFFICIAL_URL_BOUND",
        "source_health": "PASS",
    }


class RoleProfileTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(PROFILE_PATH)

    def test_repository_profiles_are_versioned_and_non_sensitive(self):
        self.assertEqual(set(self.catalog["profiles"]), {"general", "council-liaison", "traffic-policy"})
        for profile in self.catalog["profiles"].values():
            self.assertGreaterEqual(profile["version"], 1)
            self.assertRegex(profile["profile_hash"], r"^[0-9a-f]{64}$")
            self.assertNotIn("email", profile)

    def test_same_events_get_different_deterministic_views(self):
        council_event = event(
            "council-1",
            source_id="S-006",
            headline="議會質詢順序更新",
            roles=["議會聯絡"],
        )
        traffic_event = event(
            "traffic-1",
            source_id="S-032",
            headline="交通道路安全公告",
            roles=["交通業管"],
        )
        council = project_profile(
            [council_event, traffic_event],
            [],
            self.catalog["profiles"]["council-liaison"],
            ranking_policy_version=self.catalog["ranking_policy_version"],
            observed_at=NOW,
        )
        traffic = project_profile(
            [council_event, traffic_event],
            [],
            self.catalog["profiles"]["traffic-policy"],
            ranking_policy_version=self.catalog["ranking_policy_version"],
            observed_at=NOW,
        )

        self.assertEqual(council["priority_items"][0]["event_id"], "council-1")
        self.assertEqual(traffic["priority_items"][0]["event_id"], "traffic-1")
        self.assertEqual(
            council["priority_items"][0]["what_changed"],
            traffic["priority_items"][1]["what_changed"],
        )
        self.assertEqual(
            council["priority_items"][0]["official_url"],
            traffic["priority_items"][1]["official_url"],
        )
        self.assertIn("affected_role_match", council["priority_items"][0]["profile_relevance"]["reason_codes"])

    def test_deadline_weight_and_reason_are_replayable(self):
        item = event(
            "deadline-1",
            source_id="S-032",
            headline="交通管制期限",
            deadline="2026-09-23T08:00:00+08:00",
            change_type="DEADLINE_CHANGED",
        )
        profile = copy.deepcopy(self.catalog["profiles"]["traffic-policy"])
        low = rank_items([item], {**profile, "deadline_weight": 1.0}, observed_at=NOW)[0]
        high = rank_items([item], {**profile, "deadline_weight": 2.0}, observed_at=NOW)[0]
        self.assertIn("deadline_within_72h", high["profile_relevance"]["reason_codes"])
        self.assertGreater(high["profile_relevance"]["score"], low["profile_relevance"]["score"])

    def test_profile_version_changes_hash(self):
        raw = {
            "schema_version": 1,
            "ranking_policy_version": "role-ranking-v1",
            "profiles": [
                {
                    "profile_id": "general",
                    "version": 1,
                    "label": "綜合",
                    "responsibilities": ["總覽"],
                    "topics": ["警政"],
                    "source_ids": ["S-001"],
                    "role_terms": ["幕僚"],
                    "deadline_weight": 1.0,
                    "status": "active",
                }
            ],
        }
        first = validate_catalog(raw)["profiles"]["general"]["profile_hash"]
        raw["profiles"][0]["version"] = 2
        second = validate_catalog(raw)["profiles"]["general"]["profile_hash"]
        self.assertNotEqual(first, second)

    def test_stale_source_is_preserved_in_profile_projection(self):
        item = event("stale-1", source_id="S-032", headline="交通公告")
        item["source_health"] = "STALE"
        item["source_gaps"] = ["freshness"]
        view = project_profile(
            [item],
            [],
            self.catalog["profiles"]["traffic-policy"],
            ranking_policy_version=self.catalog["ranking_policy_version"],
            observed_at=NOW,
        )
        self.assertEqual(view["priority_items"][0]["source_health"], "STALE")
        self.assertEqual(view["priority_items"][0]["source_gaps"], ["freshness"])

    def test_no_match_is_explicitly_default_not_silently_dropped(self):
        profile = {
            **self.catalog["profiles"]["general"],
            "topics": ["不存在的議題"],
            "source_ids": ["S-999"],
            "role_terms": ["不存在的角色"],
        }
        ranked = rank_items([event("no-match")], profile, observed_at=NOW)
        self.assertEqual(ranked[0]["profile_relevance"]["reason_codes"], ["default"])

    def test_tie_break_is_source_then_event_id(self):
        profile = {
            **self.catalog["profiles"]["general"],
            "topics": [],
            "source_ids": [],
            "role_terms": [],
        }
        ranked = rank_items(
            [event("b", source_id="S-001"), event("a", source_id="S-001")],
            profile,
            observed_at=NOW,
        )
        self.assertEqual([item["event_id"] for item in ranked], ["a", "b"])

    def test_sensitive_profile_field_fails_closed(self):
        raw = {
            "schema_version": 1,
            "ranking_policy_version": "role-ranking-v1",
            "profiles": [
                {
                    "profile_id": "general",
                    "version": 1,
                    "label": "綜合",
                    "responsibilities": ["總覽"],
                    "topics": [],
                    "source_ids": [],
                    "role_terms": [],
                    "deadline_weight": 1.0,
                    "status": "active",
                    "email": "person@example.gov.tw",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            validate_catalog(raw)

    def test_builder_emits_same_event_in_each_profile_view(self):
        change = ChangeEvent(
            "E-1",
            "S-032:traffic-1",
            "S-032",
            "traffic-1",
            "交通道路安全公告",
            "https://example.gov.tw/traffic-1",
            "REVISED",
            NOW,
            None,
            "UNVERIFIED_DATE",
            "FIRST_SEEN",
            None,
            1,
            ("title",),
            True,
            "交通道路安全異動",
        )
        brief = {"overview": {"current_change_count": 1}, "status_message": "有變更"}
        BUILD.enrich_for_police_users(
            brief,
            [change],
            handoff_state=empty_state(),
            current_items={},
            source_status={"sources": []},
            profile_id="traffic-policy",
            observed_at=NOW,
        )

        self.assertEqual(brief["profile"]["profile_id"], "traffic-policy")
        views = {view["profile"]["profile_id"]: view for view in brief["profile_views"]}
        self.assertEqual(set(views), {"general", "council-liaison", "traffic-policy"})
        self.assertEqual(
            {view["priority_items"][0]["event_id"] for view in views.values()},
            {"E-1"},
        )
        self.assertEqual(
            {view["priority_items"][0]["official_url"] for view in views.values()},
            {"https://example.gov.tw/traffic-1"},
        )


if __name__ == "__main__":
    unittest.main()
