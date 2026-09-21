import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import online_collect as collector
from intel_v2.semantics import ChangeEvent


ROOT = Path(__file__).resolve().parents[1]
V2_SCRIPT = ROOT / "scripts" / "build-v2-shadow-brief.py"
spec = importlib.util.spec_from_file_location("build_v2_shadow_brief", V2_SCRIPT)
v2 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(v2)


class CandidatePublicationWiringTests(unittest.TestCase):
    def test_promoted_candidate_uses_the_same_demo_status_and_feed_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-status.json"
            active_sources = dict(collector.P0_SOURCES)
            active_sources["S-032"] = (
                "臺中市政府交通局最新消息",
                "https://www.traffic.taichung.gov.tw/",
            )

            def fake_collect(_session, source_id, _start, _end, *_args, **_kwargs):
                name, source_url = active_sources[source_id]
                return {
                    "source_health": "PASS",
                    "window_completeness": "COMPLETE_WITH_ITEMS",
                    "window_item_count": 1,
                    "snapshot_item_count": 1,
                    "snapshots": [{"http_status": 200, "purpose": "LIST"}],
                    "manifest_sha256": source_id.lower().replace("-", "") * 16,
                    "items": [{
                        "stable_key": f"{source_id}-1",
                        "source_url": f"{source_url.rstrip('/')}/item",
                        "published_at": "2026-09-11T00:00:00+08:00",
                        "content_sha256": (source_id.lower().replace("-", "") * 16)[:64],
                        "payload": {"title": f"{name}公告"},
                    }],
                }

            with mock.patch.object(collector, "P0_SOURCES", active_sources), mock.patch.object(
                collector, "collect_source", side_effect=fake_collect
            ):
                collector.build_demo_status(output, "MORNING", date(2026, 9, 11), "manual")

            status = json.loads(output.read_text(encoding="utf-8"))
            feed = json.loads((output.parent / "intelligence-feed.json").read_text(encoding="utf-8"))
            candidate_status = next(row for row in status["sources"] if row["source_id"] == "S-032")
            candidate_item = next(row for row in feed["items"] if row["source_id"] == "S-032")
            self.assertEqual(candidate_status["source_name"], "臺中市政府交通局最新消息")
            self.assertEqual(candidate_item["source_name"], "臺中市政府交通局最新消息")
            self.assertEqual(candidate_item["official_url"], "https://www.traffic.taichung.gov.tw/item")

    def test_promoted_news_candidate_keeps_source_identity_in_feed_projection(self):
        item = {
            "stable_key": "632",
            "source_url": "https://www.traffic.taichung.gov.tw/news/index.asp?Parser=9,4,632",
            "published_at": "2026-09-11T00:00:00+08:00",
            "content_sha256": "a" * 64,
            "payload": {"title": "交通局公告"},
        }
        projected = collector.project_feed_item(
            item,
            "S-032",
            "臺中市政府交通局最新消息",
            "https://www.traffic.taichung.gov.tw/",
            "FRESH",
            "PASS",
            "COMPLETE_WITH_ITEMS",
            item["published_at"],
            "2026-09-11T01:00:00+08:00",
            set(),
        )
        self.assertEqual(projected["source_id"], "S-032")
        self.assertEqual(projected["source_name"], "臺中市政府交通局最新消息")
        self.assertEqual(projected["official_url"], item["source_url"])

    def test_v2_context_names_all_first_promotion_candidates(self):
        expected = {
            "S-001": "臺中市政府警察局警政新聞",
            "S-019": "臺中市政府市政會議紀錄與專案報告",
            "S-032": "臺中市政府交通局最新消息",
        }
        self.assertEqual(
            {source_id: v2.SOURCE_CONTEXT[source_id]["source_name"] for source_id in expected},
            expected,
        )

    def test_v2_projection_keeps_candidate_name_and_official_locator(self):
        event = ChangeEvent(
            event_id="EV-S032-1",
            identity="S-032:632",
            source_id="S-032",
            stable_key="632",
            title="交通局公告",
            official_url="https://www.traffic.taichung.gov.tw/item",
            change_type="NEW",
            detected_at="2026-09-11T01:00:00+08:00",
            occurred_at="2026-09-11T00:00:00+08:00",
            date_status="KNOWN",
            temporal_basis="OFFICIAL_DATE",
            before_version=None,
            after_version=1,
            changed_fields=("title",),
            publishable=True,
            wording="新增官方公告",
        )
        projected = v2.event_projection(event, "TOP")
        self.assertEqual(projected["source_name"], "臺中市政府交通局最新消息")
        self.assertEqual(projected["official_url"], event.official_url)


if __name__ == "__main__":
    unittest.main()
