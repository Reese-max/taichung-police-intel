from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import online_collect

from scripts import schema_drift as drift


API = {
    "success": True,
    "data": {
        "data": [{
            "proceedingsId": "p1",
            "date": "2026-09-10T00:00:00",
            "speaker": "甲",
            "content": "內容",
        }],
        "totalPages": 1,
        "totalCount": 1,
    },
}

RSS = '''<?xml version="1.0"?><rss version="2.0"><channel>
<item iCuItem="3375296"><title>活動公告</title><link>https://official.test/3375296/post</link><pubDate>Mon, 21 Sep 2026 02:57:54 GMT</pubDate></item>
</channel></rss>'''.encode("utf-8")

FIRE_LIVE = """<div class="update">最後異動時間：2026-09-21 19:24:23</div>
<ul class="list rwd-table"><li class="list_head">標題</li><li>
<span data-th="受理時間：">2026/09/21 19:21:44</span><span data-th="案類：">緊急救護</span>
<span data-th="案別">車禍</span><span data-th="發生地點：">北屯區軍榮二街</span>
<span data-th="派遣分隊：">東山分隊</span><span data-th="執行狀況："></span></li></ul>""".encode("utf-8")


class SchemaDriftTests(unittest.TestCase):
    def test_all_required_news_contracts_use_real_parser_shapes(self):
        html = {
            "S-001": '<li><a href="news_view.jsp?dataserno=1">警政新聞 115-09-10</a></li>',
            "S-019": '<li><a href="/12047/12142/12186/873338/post">會議紀錄</a></li>',
            "S-032": '<li><a href="index-1.asp?Parser=9,4,20,,,,21750">交通消息 115-09-10</a></li>',
        }
        for source_id, body in html.items():
            with self.subTest(source_id=source_id):
                result = drift.observe(
                    drift.CONTRACTS[source_id],
                    body,
                    content_type="text/html; charset=utf-8",
                    final_url="https://official.test/list",
                )
                self.assertEqual(result["status"], "NO_DRIFT")
                self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")
        result = drift.observe(
            drift.CONTRACTS["S-033"], RSS, content_type="application/xml", final_url="https://official.test/rss"
        )
        self.assertEqual(result["status"], "NO_DRIFT")
        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")
        result = drift.observe(
            drift.CONTRACTS["S-031"], FIRE_LIVE, content_type="text/html", final_url="https://official.test/caselist"
        )
        self.assertEqual(result["status"], "NO_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")

    def test_rss_shape_break_is_fail_closed(self):
        result = drift.observe(
            drift.CONTRACTS["S-033"], b"<rss><channel><item><title>missing identity</title></item></channel></rss>",
            content_type="application/xml",
        )
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")

    def test_fire_live_shape_break_is_fail_closed(self):
        result = drift.observe(
            drift.CONTRACTS["S-031"], FIRE_LIVE.replace("最後異動時間".encode(), "頁面更新".encode()), content_type="text/html"
        )
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertTrue(result["review_required"])

    def test_http_200_selector_failure_is_breaking_and_partial(self):
        result = drift.observe(
            drift.CONTRACTS["S-001"],
            "<html><body>success but unrelated markup</body></html>",
            content_type="text/html",
        )
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertTrue(result["review_required"])

    def test_api_error_object_and_missing_pagination_fail_closed(self):
        error = drift.observe(
            drift.CONTRACTS["S-007"],
            json.dumps({"error": "temporarily unavailable"}),
            content_type="application/json",
        )
        self.assertEqual(error["status"], "BREAKING_DRIFT")

        no_page = copy.deepcopy(API)
        no_page["data"].pop("totalPages")
        result = drift.observe(
            drift.CONTRACTS["S-007"], json.dumps(no_page), content_type="application/json"
        )
        self.assertIn("PAGINATION_MARKER_MISSING", result["reasons"])
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")

    def test_api_type_change_breaks_and_new_field_is_additive(self):
        changed = copy.deepcopy(API)
        changed["data"]["data"][0]["proceedingsId"] = 1
        result = drift.observe(
            drift.CONTRACTS["S-007"], json.dumps(changed), content_type="application/json"
        )
        self.assertEqual(result["status"], "BREAKING_DRIFT")

        added = copy.deepcopy(API)
        added["data"]["data"][0]["newField"] = "safe"
        result = drift.observe(
            drift.CONTRACTS["S-007"], json.dumps(added), content_type="application/json"
        )
        self.assertEqual(result["status"], "ADDITIVE_COMPATIBLE")
        self.assertFalse(result["review_required"])

    def test_data_gov_resource_change_is_explicit_and_required_field_loss_breaks(self):
        rows = [{"項目": "x", "欄位名稱": "y", "數值": "1", "資料時間日期": "2026-09-01", "資料週期": "月"}]
        first = drift.observe(
            drift.CONTRACTS["S-028"], json.dumps(rows), content_type="application/json", resource_id="r1"
        )
        changed_resource = drift.observe(
            drift.CONTRACTS["S-028"], json.dumps(rows), content_type="application/json", resource_id="r2", previous=first
        )
        self.assertEqual(changed_resource["status"], "ADDITIVE_COMPATIBLE")
        self.assertTrue(changed_resource["resource_id_changed"])
        self.assertIn("RESOURCE_ID_CHANGED", changed_resource["reasons"])
        missing_resource = drift.observe(
            drift.CONTRACTS["S-028"], json.dumps(rows), content_type="application/json"
        )
        self.assertEqual(missing_resource["status"], "BREAKING_DRIFT")
        self.assertIn("RESOURCE_ID_MISSING", missing_resource["reasons"])

        missing = rows[0].copy()
        missing.pop("數值")
        result = drift.observe(
            drift.CONTRACTS["S-028"], json.dumps([missing]), content_type="application/json", resource_id="r2"
        )
        self.assertEqual(result["status"], "BREAKING_DRIFT")

    def test_csv_header_drift_and_additive_column(self):
        good = "民國年月,網域,網站性質,法律依據,聲請單位\n11509,a.test,其他,法規,機關\n"
        result = drift.observe(drift.CONTRACTS["CTX-165"], good, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "NO_DRIFT")
        extra = "民國年月,網域,網站性質,法律依據,聲請單位,備註\n11509,a.test,其他,法規,機關,x\n"
        result = drift.observe(drift.CONTRACTS["CTX-165"], extra, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "ADDITIVE_COMPATIBLE")
        missing = "民國年月,網域\n11509,a.test\n"
        result = drift.observe(drift.CONTRACTS["CTX-165"], missing, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "BREAKING_DRIFT")

    def test_required_record_field_missing_from_only_some_rows_fails_closed(self):
        partial = copy.deepcopy(API)
        partial["data"]["data"].append({"proceedingsId": "p2", "date": "2026-09-11", "content": "無講者"})
        result = drift.observe(drift.CONTRACTS["S-007"], json.dumps(partial), content_type="application/json")
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertTrue(result["review_required"])
        self.assertIn("REQUIRED_FIELD_MISSING_IN_SOME_ROWS", result["reasons"])
        self.assertIn("MISSING_IN_SOME_ROWS_speaker", result["reasons"])

    def test_data_gov_required_field_missing_from_only_some_rows_fails_closed(self):
        rows = [
            {"項目": "x", "欄位名稱": "y", "數值": "1", "資料時間日期": "2026-09-01", "資料週期": "月"},
            {"項目": "z", "欄位名稱": "w", "資料時間日期": "2026-09-01", "資料週期": "月"},
        ]
        result = drift.observe(
            drift.CONTRACTS["S-028"], json.dumps(rows), content_type="application/json", resource_id="r1"
        )
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertTrue(result["review_required"])
        self.assertIn("REQUIRED_FIELD_MISSING_IN_SOME_ROWS", result["reasons"])
        self.assertIn("MISSING_IN_SOME_ROWS_數值", result["reasons"])

    def test_csv_row_column_count_drift_is_breaking(self):
        header = "民國年月,網域,網站性質,法律依據,聲請單位\n"
        truncated = drift.observe(
            drift.CONTRACTS["CTX-165"], header + "11509,a.test\n", content_type="text/csv", resource_id="r1"
        )
        self.assertEqual(truncated["status"], "BREAKING_DRIFT")
        self.assertEqual(truncated["window_completeness"], "PARTIAL")
        self.assertTrue(truncated["review_required"])
        self.assertIn("ROW_COLUMN_COUNT_MISMATCH", truncated["reasons"])

        shifted = drift.observe(
            drift.CONTRACTS["CTX-165"], header + '11509,"a,b,其他,法規,機關\n', content_type="text/csv", resource_id="r1"
        )
        self.assertEqual(shifted["status"], "BREAKING_DRIFT")
        self.assertIn("ROW_COLUMN_COUNT_MISMATCH", shifted["reasons"])

        unchanged = drift.observe(
            drift.CONTRACTS["CTX-165"], header + "11509,a.test,其他,法規,機關\n", content_type="text/csv", resource_id="r1"
        )
        self.assertEqual(unchanged["status"], "NO_DRIFT")

    def test_observed_schema_fingerprint_tracks_shape_not_data_volume(self):
        def api_payload(total_pages: int, total_count: int) -> str:
            return json.dumps({
                "success": True,
                "data": {
                    "data": [{"proceedingsId": "p1", "date": "2026-09-10T00:00:00", "speaker": "甲", "content": "內容"}],
                    "totalPages": total_pages,
                    "totalCount": total_count,
                },
            })

        one_page = drift.observe(drift.CONTRACTS["S-007"], api_payload(1, 1), content_type="application/json")
        many_pages = drift.observe(drift.CONTRACTS["S-007"], api_payload(9, 812), content_type="application/json")
        self.assertEqual(one_page["status"], "NO_DRIFT")
        self.assertEqual(many_pages["status"], "NO_DRIFT")
        self.assertEqual(one_page["observed_schema_fingerprint"], many_pages["observed_schema_fingerprint"])

        list_url = "https://official.test/list"
        one_item = drift.observe(
            drift.CONTRACTS["S-001"],
            '<li><a href="news_view.jsp?dataserno=1">新聞 115-09-10</a></li>',
            content_type="text/html",
            final_url=list_url,
        )
        two_items = drift.observe(
            drift.CONTRACTS["S-001"],
            '<li><a href="news_view.jsp?dataserno=1">新聞 115-09-10</a></li>'
            '<li><a href="news_view.jsp?dataserno=2">新聞 115-09-11</a></li>',
            content_type="text/html",
            final_url=list_url,
        )
        self.assertEqual(one_item["status"], "NO_DRIFT")
        self.assertEqual(two_items["status"], "NO_DRIFT")
        self.assertEqual(one_item["observed_schema_fingerprint"], two_items["observed_schema_fingerprint"])

        csv_one = "民國年月,網域,網站性質,法律依據,聲請單位\n11509,a.test,其他,法規,機關\n"
        csv_many = csv_one + "11510,b.test,其他,法規,機關\n11511,c.test,其他,法規,機關\n"
        self.assertEqual(
            drift.observe(drift.CONTRACTS["CTX-165"], csv_one, content_type="text/csv", resource_id="r1")["observed_schema_fingerprint"],
            drift.observe(drift.CONTRACTS["CTX-165"], csv_many, content_type="text/csv", resource_id="r1")["observed_schema_fingerprint"],
        )

        retyped = json.loads(api_payload(1, 1))
        retyped["data"]["data"][0]["speaker"] = 7
        drifted = drift.observe(drift.CONTRACTS["S-007"], json.dumps(retyped), content_type="application/json")
        self.assertEqual(drifted["status"], "BREAKING_DRIFT")
        self.assertNotEqual(drifted["observed_schema_fingerprint"], one_page["observed_schema_fingerprint"])

    def test_observed_schema_fingerprint_reports_the_shape_it_observed(self):
        def api_payload(speaker: object) -> str:
            return json.dumps({
                "success": True,
                "data": {
                    "data": [{"proceedingsId": "p1", "date": "2026-09-10T00:00:00", "speaker": speaker, "content": "內容"}],
                    "totalPages": 1,
                    "totalCount": 1,
                },
            })

        expected = {
            "source_id": "S-007",
            "transport": "JSON_API",
            "record_fields": ["content", "date", "proceedingsId", "speaker"],
            "record_types": {
                "content": ["string"],
                "date": ["string"],
                "proceedingsId": ["string"],
                "speaker": ["string"],
            },
            "pagination": {"data.totalCount": "integer", "data.totalPages": "integer"},
        }
        observed = drift.observe(drift.CONTRACTS["S-007"], api_payload("甲"), content_type="application/json")
        self.assertEqual(expected, observed["fingerprint_signature"])
        self.assertEqual(observed["observed_schema_fingerprint"], drift.canonical_hash(expected))

    def test_partly_missing_and_retyped_fields_report_both_in_one_verdict(self):
        payload = copy.deepcopy(API)
        payload["data"]["data"].append({"proceedingsId": 2, "date": "2026-09-11", "content": "無講者"})
        result = drift.observe(drift.CONTRACTS["S-007"], json.dumps(payload), content_type="application/json")
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertIn("REQUIRED_FIELD_MISSING_IN_SOME_ROWS", result["reasons"])
        self.assertIn("MISSING_IN_SOME_ROWS_speaker", result["reasons"])
        self.assertIn("TYPE_CHANGED", result["reasons"])
        self.assertIn("TYPE_proceedingsId", result["reasons"])
        self.assertEqual(
            result["fingerprint_signature"]["record_types"]["proceedingsId"], ["integer", "string"]
        )

    def test_single_page_of_a_larger_window_is_not_a_complete_window(self):
        one_of_many = copy.deepcopy(API)
        one_of_many["data"]["totalPages"] = 9
        one_of_many["data"]["totalCount"] = 1800
        result = drift.observe(
            drift.CONTRACTS["S-007"], json.dumps(one_of_many), content_type="application/json"
        )
        self.assertEqual(result["status"], "NO_DRIFT")
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertIn("PAGINATION_WINDOW_NOT_COVERED", result["reasons"])
        self.assertEqual(result["observed_record_count"], 1)
        self.assertEqual(result["declared_total_count"], 1800)
        self.assertFalse(result["review_required"])

        covered = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        self.assertEqual(covered["window_completeness"], "COMPLETE_WITH_ITEMS")
        self.assertNotIn("PAGINATION_WINDOW_NOT_COVERED", covered["reasons"])
        self.assertEqual(covered["declared_total_count"], 1)

    def test_csv_header_with_only_blank_rows_is_not_a_complete_window(self):
        for body in (
            "民國年月,網域,網站性質,法律依據,聲請單位\n\n",
            "民國年月,網域,網站性質,法律依據,聲請單位\n\r\n\r\n",
        ):
            with self.subTest(body=body):
                result = drift.observe(
                    drift.CONTRACTS["CTX-165"], body, content_type="text/csv", resource_id="r1"
                )
                self.assertEqual(result["status"], "CONTENT_SHAPE_UNKNOWN")
                self.assertEqual(result["window_completeness"], "PARTIAL")
                self.assertEqual(result["reasons"], ["NO_DATA_ROWS"])

    def test_csv_row_reasons_name_the_file_row_and_stay_bounded(self):
        header = "民國年月,網域,網站性質,法律依據,聲請單位\n"
        good = "11509,a.test,其他,法規,機關\n"
        body = header + good + "\n" + "11509,short\n" + good
        result = drift.observe(drift.CONTRACTS["CTX-165"], body, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertIn("ROW_COLUMN_COUNT_MISMATCH", result["reasons"])
        self.assertIn("MISMATCHED_ROW_COUNT_1", result["reasons"])
        self.assertIn("ROW_3_COLUMNS_2", result["reasons"])
        self.assertNotIn("ROW_2_COLUMNS_2", result["reasons"])

        noisy = drift.observe(
            drift.CONTRACTS["CTX-165"], header + "11509,short\n" * 500, content_type="text/csv", resource_id="r1"
        )
        row_reasons = [
            reason for reason in noisy["reasons"]
            if reason.startswith("ROW_") and reason != "ROW_COLUMN_COUNT_MISMATCH"
        ]
        self.assertEqual(len(row_reasons), drift.MAX_ROW_REASONS)
        self.assertIn("MISMATCHED_ROW_COUNT_500", noisy["reasons"])
        self.assertLessEqual(len(noisy["reasons"]), drift.MAX_ROW_REASONS + 2)

    def test_large_well_formed_csv_stays_no_drift(self):
        header = "民國年月,網域,網站性質,法律依據,聲請單位\n"
        body = header + "".join(
            f"115{index % 100:02d},host{index}.test,其他,法規,機關\n" for index in range(5000)
        )
        result = drift.observe(drift.CONTRACTS["CTX-165"], body, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "NO_DRIFT")
        self.assertEqual(result["reasons"], [])

    def test_fingerprint_is_shape_only_across_content_type_parameters_and_sources(self):
        body = json.dumps(API)
        plain = drift.observe(drift.CONTRACTS["S-007"], body, content_type="application/json")
        parametrized = drift.observe(drift.CONTRACTS["S-007"], body, content_type="application/json; charset=utf-8")
        self.assertEqual(
            plain["observed_schema_fingerprint"], parametrized["observed_schema_fingerprint"]
        )

        mismatch = drift.observe(drift.CONTRACTS["S-007"], b"<html />", content_type="text/html; charset=big5")
        other_mismatch = drift.observe(drift.CONTRACTS["S-007"], b"<html />", content_type="text/html;q=0.9")
        self.assertEqual(
            mismatch["observed_schema_fingerprint"], other_mismatch["observed_schema_fingerprint"]
        )

        broken_html = "<html><body>200 but changed</body></html>"
        fingerprints = {
            drift.observe(contract, broken_html, content_type="text/html")["observed_schema_fingerprint"]
            for contract in (drift.CONTRACTS["S-001"], drift.CONTRACTS["S-019"], drift.CONTRACTS["S-032"])
        }
        self.assertEqual(len(fingerprints), 3)

    def test_every_fingerprinted_outcome_records_the_signature_it_hashed(self):
        api_result = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        self.assertIsNotNone(api_result["fingerprint_signature"])
        for contract, body, content_type in (
            (drift.CONTRACTS["S-001"], b"<html>no list</html>", "text/html"),
            (drift.CONTRACTS["S-033"], b"<rss><channel></channel></rss>", "application/xml"),
            (drift.CONTRACTS["S-031"], b"<div>no live list</div>", "text/html"),
            (drift.CONTRACTS["S-007"], b"not-json", "application/json"),
        ):
            with self.subTest(source_id=contract["source_id"]):
                result = drift.observe(contract, body, content_type=content_type)
                self.assertIsNotNone(result["observed_schema_fingerprint"])
                self.assertIsNotNone(result["fingerprint_signature"])
                self.assertEqual(
                    result["observed_schema_fingerprint"],
                    drift.canonical_hash(result["fingerprint_signature"]),
                )

        for contract, kwargs in (
            (drift.CONTRACTS["S-009"], {"http_status": 503, "content_type": "application/json"}),
            (drift.CONTRACTS["S-028"], {"http_status": 503, "content_type": "application/json"}),
        ):
            with self.subTest(source_id=contract["source_id"]):
                result = drift.observe(contract, b"", **kwargs)
                self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
                self.assertIsNone(result["fingerprint_signature"])
        unknown = drift._unknown(drift.CONTRACTS["S-007"], "NO_CURRENT_OBSERVATION")
        self.assertIsNone(unknown["fingerprint_signature"])

    def test_fire_live_fingerprint_ignores_incident_count(self):
        one = drift.observe(drift.CONTRACTS["S-031"], FIRE_LIVE, content_type="text/html")
        two = drift.observe(
            drift.CONTRACTS["S-031"],
            FIRE_LIVE + FIRE_LIVE.split(b"<ul", 1)[1],
            content_type="text/html",
        )
        self.assertEqual(one["status"], "NO_DRIFT")
        self.assertEqual(two["status"], "NO_DRIFT")
        self.assertEqual(one["observed_schema_fingerprint"], two["observed_schema_fingerprint"])

    def test_every_emitted_record_carries_the_module_contract_version(self):
        # Guards the shape-only fingerprint era: the version travels with each
        # observation so a persisted fingerprint can be placed in time.
        version = drift.CONTRACT_VERSION
        self.assertEqual(drift.empty_state()["contract_version"], version)
        observed = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        self.assertEqual(observed["contract_version"], version)
        state = drift.update_state(drift.empty_state(), observed)
        self.assertEqual(state["contract_version"], version)
        for record in (state["sources"]["S-007"]["current"], state["sources"]["S-007"]["last_known_good"]):
            self.assertEqual(record["contract_version"], version)
        broken = drift.observe(drift.CONTRACTS["S-007"], b"not-json", content_type="application/json")
        self.assertEqual(broken["contract_version"], version)
        unavailable = drift.observe(drift.CONTRACTS["S-009"], b"", http_status=503)
        self.assertEqual(unavailable["contract_version"], version)

    def test_replay_observations_are_validated_before_reaching_the_receipt(self):
        sample = {"source_id": "S-007", "body": json.dumps(API), "content_type": "application/json"}
        for bad in (
            {**sample, "observed_at": "not-a-date"},
            {**sample, "resource_id": {"nested": 1}},
            {**sample, "http_status": "200"},
            {**sample, "http_status": True},
            {**sample, "final_url": ["https://official.test"]},
            {**sample, "bogus": "injected"},
        ):
            with self.subTest(bad=sorted(set(bad) - {"body"})):
                with self.assertRaisesRegex(ValueError, "observation has"):
                    drift.build_receipt([bad], contracts={"S-007": drift.CONTRACTS["S-007"]})
        accepted = {**sample, "resource_id": None, "requested_url": None, "error_reason": "LIVE_FETCH_TIMEOUT"}
        receipt, _ = drift.build_receipt([accepted], contracts={"S-007": drift.CONTRACTS["S-007"]})
        self.assertEqual(receipt["sources"][0]["source_id"], "S-007")

    def test_state_marks_the_current_contract_version_over_a_persisted_older_one(self):
        persisted = drift.empty_state()
        persisted["contract_version"] = "1.0"
        persisted["sources"] = {
            "S-007": {
                "current": {"contract_version": "1.0", "observed_schema_fingerprint": "a" * 64, "status": "NO_DRIFT"},
                "last_known_good": {"contract_version": "1.0", "observed_schema_fingerprint": "a" * 64},
                "history": [],
            }
        }
        observed = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        updated = drift.update_state(persisted, observed)
        self.assertEqual(updated["contract_version"], "1.1")
        self.assertEqual(updated["sources"]["S-007"]["last_known_good"]["contract_version"], "1.1")
        self.assertEqual(updated["sources"]["S-007"]["history"][0]["contract_version"], "1.0")
        self.assertEqual(updated["sources"]["S-007"]["history"][0]["observed_schema_fingerprint"], "a" * 64)

    def test_review_inbox_carries_the_last_known_good_the_reviewer_needs(self):
        from intel_v2.review import schema_drift_candidates

        good = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        state = drift.update_state(drift.empty_state(), good)
        receipt, _ = drift.build_receipt(
            [{"source_id": "S-007", "body": b'{"error": "changed"}', "content_type": "application/json"}],
            state=state,
            contracts={"S-007": drift.CONTRACTS["S-007"]},
        )
        self.assertEqual(len(receipt["review_inbox"]), 1)
        self.assertEqual(
            receipt["review_inbox"][0]["last_known_good"]["observed_schema_fingerprint"],
            good["observed_schema_fingerprint"],
        )
        candidates = schema_drift_candidates(receipt)
        self.assertEqual(len(candidates), 1)
        self.assertIsNotNone(candidates[0]["evidence"]["before"])
        self.assertEqual(
            candidates[0]["evidence"]["before"]["observed_schema_fingerprint"],
            good["observed_schema_fingerprint"],
        )
        self.assertEqual(candidates[0]["evidence"]["after"]["status"], "BREAKING_DRIFT")

    def test_only_delimiter_free_lines_count_as_csv_padding(self):
        header = "民國年月,網域,網站性質,法律依據,聲請單位\n"
        good = "11509,a.test,其他,法規,機關\n"
        for separator in ("   \n", " \t \n", "\r\n"):
            with self.subTest(separator=separator):
                result = drift.observe(
                    drift.CONTRACTS["CTX-165"], header + good + separator, content_type="text/csv", resource_id="r1"
                )
                self.assertEqual(result["status"], "NO_DRIFT")
                self.assertEqual(result["reasons"], [])
        blank_body = drift.observe(
            drift.CONTRACTS["CTX-165"], header + "   \n", content_type="text/csv", resource_id="r1"
        )
        self.assertEqual(blank_body["status"], "CONTENT_SHAPE_UNKNOWN")
        self.assertEqual(blank_body["reasons"], ["NO_DATA_ROWS"])
        empty_values = drift.observe(
            drift.CONTRACTS["CTX-165"], header + ",,,,\n", content_type="text/csv", resource_id="r1"
        )
        self.assertEqual(empty_values["status"], "NO_DRIFT")
        # A line that carries delimiters is a record even when every cell is
        # blank, so a short all-blank row cannot pass as a complete window.
        short_blank = drift.observe(
            drift.CONTRACTS["CTX-165"], header + good + ",,,\n", content_type="text/csv", resource_id="r1"
        )
        self.assertEqual(short_blank["status"], "BREAKING_DRIFT")
        self.assertIn("ROW_COLUMN_COUNT_MISMATCH", short_blank["reasons"])

    def test_unreadable_csv_only_fails_its_own_source(self):
        header = "民國年月,網域,網站性質,法律依據,聲請單位\n"
        oversized = header + "11509," + ("x" * 200_000) + ",其他,法規,機關\n"
        result = drift.observe(drift.CONTRACTS["CTX-165"], oversized, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "CONTENT_SHAPE_UNKNOWN")
        self.assertEqual(result["reasons"], ["UNPARSEABLE_CSV"])
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertTrue(result["review_required"])

        receipt, _ = drift.build_receipt(
            [{"source_id": "CTX-165", "body": oversized, "content_type": "text/csv", "resource_id": "r1"}]
        )
        self.assertEqual(receipt["overall"], "UNKNOWN")
        self.assertEqual(len(receipt["sources"]), len(drift.CONTRACTS))

    def test_row_column_counts_in_the_signature_stay_bounded(self):
        header = "民國年月,網域,網站性質,法律依據,聲請單位\n"
        body = header + "".join(f"{','.join(['x'] * width)}\n" for width in range(1, 40))
        result = drift.observe(drift.CONTRACTS["CTX-165"], body, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertLessEqual(
            len(result["fingerprint_signature"]["row_column_counts"]), drift.MAX_ROW_REASONS
        )

    def test_wide_csv_header_is_capped_in_the_signature_but_not_the_verdict(self):
        required = ["民國年月", "網域", "網站性質", "法律依據", "聲請單位"]
        wide = required + [f"備註{index:04d}" for index in range(drift.MAX_SIGNATURE_NAMES)]
        header = ",".join(wide) + "\n"
        body = header + ",".join(["x"] * len(wide)) + "\n"
        result = drift.observe(drift.CONTRACTS["CTX-165"], body, content_type="text/csv", resource_id="r1")
        self.assertEqual(result["status"], "ADDITIVE_COMPATIBLE")
        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")
        self.assertEqual(len(result["fingerprint_signature"]["header"]), drift.MAX_SIGNATURE_NAMES)

        truncated = drift.observe(
            drift.CONTRACTS["CTX-165"],
            header + ",".join(["x"] * (len(wide) - 1)) + "\n",
            content_type="text/csv",
            resource_id="r1",
        )
        self.assertEqual(truncated["status"], "BREAKING_DRIFT")
        self.assertIn("ROW_COLUMN_COUNT_MISMATCH", truncated["reasons"])
        self.assertEqual(len(truncated["fingerprint_signature"]["header"]), drift.MAX_SIGNATURE_NAMES)
        self.assertLessEqual(len(truncated["reasons"]), drift.MAX_ROW_REASONS + 2)

    def test_live_runs_persist_state_so_last_known_good_survives_to_the_receipt(self):
        observations = [
            {"source_id": "S-007", "body": json.dumps(API).encode(), "http_status": 200,
             "content_type": "application/json", "resource_id": None,
             "observed_at": "2026-09-10T00:00:00+00:00", "requested_url": None, "final_url": None},
        ]
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            output_path = Path(directory) / "receipt.json"
            with mock.patch.object(drift, "live_observations", return_value=observations):
                self.assertEqual(
                    drift.main(["--live", "--state", str(state_path), "--output", str(output_path)]), 0
                )
            first_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(first_state["contract_version"], "1.1")
            good_fingerprint = first_state["sources"]["S-007"]["last_known_good"]["observed_schema_fingerprint"]
            self.assertIsNotNone(good_fingerprint)

            broken = [{**observations[0], "body": b'{"error": "changed"}', "observed_at": "2026-09-11T00:00:00+00:00"}]
            with mock.patch.object(drift, "live_observations", return_value=broken):
                self.assertEqual(
                    drift.main(["--live", "--state", str(state_path), "--output", str(output_path)]), 0
                )
            receipt = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["review_inbox"][0]["last_known_good"]["observed_schema_fingerprint"], good_fingerprint)
            second_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                second_state["sources"]["S-007"]["last_known_good"]["observed_schema_fingerprint"], good_fingerprint
            )
            self.assertEqual(second_state["sources"]["S-007"]["history"][0]["observed_schema_fingerprint"], good_fingerprint)

    def test_naive_observed_at_is_rejected_before_it_can_drop_the_review_inbox(self):
        from intel_v2.review import schema_drift_candidates, reconcile, empty_state as review_state

        sample = {
            "source_id": "S-007",
            "body": b'{"error": "changed"}',
            "content_type": "application/json",
            "observed_at": "2026-09-10T00:00:00",
        }
        with self.assertRaisesRegex(ValueError, "observed_at needs a timezone"):
            drift.build_receipt([sample], contracts={"S-007": drift.CONTRACTS["S-007"]})

        # With a tz-aware timestamp the drift row reconciles into a reviewable
        # item; the naive form would have made intel_v2.review raise and
        # system-health silently fall back to the raw drift rows.
        sample["observed_at"] = "2026-09-10T00:00:00+00:00"
        receipt, _ = drift.build_receipt([sample], contracts={"S-007": drift.CONTRACTS["S-007"]})
        reconciled = reconcile(
            review_state(), schema_drift_candidates(receipt), observed_at="2026-09-10T00:00:00+00:00"
        )
        items = list(reconciled["items"].values())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["entity_ids"], {"source_id": "S-007"})
        self.assertEqual(items[0]["reason"], "NEEDS_REVIEW")
        naive_receipt = json.loads(json.dumps(receipt))
        naive_receipt["review_inbox"][0]["observed_at"] = "2026-09-10T00:00:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            reconcile(
                review_state(),
                schema_drift_candidates(naive_receipt),
                observed_at="2026-09-10T00:00:00+00:00",
            )

    def test_observation_bodies_must_be_decoded_before_build_receipt(self):
        sample = {"source_id": "S-007", "content_type": "application/json"}
        for key in ("body_base64", "body_text"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, f"unsupported key: {key}"):
                    drift.build_receipt([{**sample, key: "payload"}])

    def test_http_status_reaches_the_receipt_as_evidence(self):
        unavailable = drift.observe(
            drift.CONTRACTS["S-009"], b"", http_status=503, content_type="application/json"
        )
        self.assertEqual(unavailable["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(unavailable["http_status"], 503)
        healthy = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        self.assertEqual(healthy["http_status"], 200)

    def test_remote_names_in_the_signature_are_capped_without_changing_the_verdict(self):
        wide = {f"欄位{index:04d}": "x" for index in range(drift.MAX_SIGNATURE_NAMES + 50)}
        wide.update({"項目": "x", "欄位名稱": "y", "數值": "1", "資料時間日期": "2026-09-01", "資料週期": "月"})
        rows = [dict(wide)]
        result = drift.observe(
            drift.CONTRACTS["S-028"], json.dumps(rows), content_type="application/json", resource_id="r1"
        )
        self.assertEqual(result["status"], "ADDITIVE_COMPATIBLE")
        self.assertEqual(
            len(result["fingerprint_signature"]["record_fields"]), drift.MAX_SIGNATURE_NAMES
        )
        self.assertEqual(
            len(result["fingerprint_signature"]["field_types"]), drift.MAX_SIGNATURE_NAMES
        )
        still_break = drift.observe(
            drift.CONTRACTS["S-028"],
            json.dumps([{key: value for key, value in rows[0].items() if key != "數值"}]),
            content_type="application/json",
            resource_id="r1",
        )
        self.assertEqual(still_break["status"], "BREAKING_DRIFT")
        self.assertIn("MISSING_數值", still_break["reasons"])

    def test_observation_previous_key_is_rejected_before_it_reaches_observe(self):
        sample = {"source_id": "S-007", "body": json.dumps(API), "content_type": "application/json"}
        with self.assertRaisesRegex(ValueError, "unknown keys: previous"):
            drift.build_receipt([{**sample, "previous": {"status": "NO_DRIFT"}}])

    def test_resource_id_drift_survives_an_unavailable_run_in_between(self):
        rows = [{"項目": "x", "欄位名稱": "y", "數值": "1", "資料時間日期": "2026-09-01", "資料週期": "月"}]
        contracts = {"S-028": drift.CONTRACTS["S-028"]}
        body = json.dumps(rows)

        receipt, state = drift.build_receipt(
            [{"source_id": "S-028", "body": body, "content_type": "application/json", "resource_id": "resource-A"}],
            contracts=contracts,
        )
        self.assertEqual(receipt["sources"][0]["status"], "NO_DRIFT")
        self.assertEqual(receipt["sources"][0]["resource_id"], "resource-A")

        # A failed probe records no resource_id, but it must not become the baseline.
        receipt, state = drift.build_receipt(
            [{"source_id": "S-028", "body": b"", "http_status": 503, "content_type": "application/json",
              "resource_id": None, "error_reason": "HTTP_503"}],
            state=state,
            contracts=contracts,
        )
        self.assertEqual(receipt["sources"][0]["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(
            state["sources"]["S-028"]["last_known_good"]["resource_id"], "resource-A"
        )

        receipt, state = drift.build_receipt(
            [{"source_id": "S-028", "body": body, "content_type": "application/json", "resource_id": "resource-B"}],
            state=state,
            contracts=contracts,
        )
        source = receipt["sources"][0]
        self.assertTrue(source["resource_id_changed"])
        self.assertIn("RESOURCE_ID_CHANGED", source["reasons"])
        self.assertEqual(source["status"], "ADDITIVE_COMPATIBLE")
        self.assertTrue(source["review_required"])
        self.assertEqual(receipt["review_inbox"][0]["reasons"], ["RESOURCE_ID_CHANGED"])

        # The baseline advances once the change is recorded, so the next swap is
        # detected on its own terms rather than escalating forever.
        control, state = drift.build_receipt(
            [{"source_id": "S-028", "body": body, "content_type": "application/json", "resource_id": "resource-C"}],
            state=state,
            contracts=contracts,
        )
        self.assertTrue(control["sources"][0]["resource_id_changed"])
        self.assertIn("RESOURCE_ID_CHANGED", control["sources"][0]["reasons"])
        unchanged, _ = drift.build_receipt(
            [{"source_id": "S-028", "body": body, "content_type": "application/json", "resource_id": "resource-C"}],
            state=state,
            contracts=contracts,
        )
        self.assertFalse(unchanged["sources"][0]["resource_id_changed"])
        self.assertEqual(unchanged["sources"][0]["status"], "NO_DRIFT")

    def test_a_multi_page_declaration_is_never_a_complete_window(self):
        for declared_total, declared_pages in ((200, 9), (0, 9), (1, 1), (1, 2)):
            with self.subTest(total=declared_total, pages=declared_pages):
                payload = copy.deepcopy(API)
                payload["data"]["totalCount"] = declared_total
                payload["data"]["totalPages"] = declared_pages
                result = drift.observe(
                    drift.CONTRACTS["S-007"], json.dumps(payload), content_type="application/json"
                )
                self.assertEqual(result["status"], "NO_DRIFT")
                self.assertEqual(result["declared_total_count"], declared_total)
                self.assertEqual(result["declared_total_pages"], declared_pages)
                expected = "PARTIAL" if (declared_pages > 1 or 1 < declared_total) else "COMPLETE_WITH_ITEMS"
                self.assertEqual(result["window_completeness"], expected)
                self.assertEqual(
                    "PAGINATION_WINDOW_NOT_COVERED" in result["reasons"],
                    expected == "PARTIAL",
                )

    def test_durable_state_is_replaced_wholesale_and_never_left_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "nested" / "state.json"
            observed = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
            state = drift.update_state(drift.empty_state(), observed)
            drift._write_state(state_path, state)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))["contract_version"], drift.CONTRACT_VERSION
            )

            # A partial write is what a timeout mid-write would leave behind;
            # the replace must overwrite it whole and leave no scratch file.
            state_path.write_text("truncated{", encoding="utf-8")
            broken = drift.observe(drift.CONTRACTS["S-007"], b"not-json", content_type="application/json")
            drift._write_state(state_path, drift.update_state(state, broken))
            recovered = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered["sources"]["S-007"]["current"]["status"], "CONTENT_SHAPE_UNKNOWN")
            self.assertEqual(list(state_path.parent.glob("*.tmp")), [])

    def test_oversized_observation_metadata_is_refused(self):
        sample = {"source_id": "S-007", "body": json.dumps(API), "content_type": "application/json"}
        with self.assertRaisesRegex(ValueError, "oversized resource_id"):
            drift.build_receipt(
                [{**sample, "resource_id": "r" * (drift.MAX_OBSERVATION_TEXT + 1)}],
                contracts={"S-007": drift.CONTRACTS["S-007"]},
            )
        receipt, _ = drift.build_receipt(
            [{**sample, "body": b"x" * (drift.MAX_OBSERVATION_TEXT * 8)}],
            contracts={"S-007": drift.CONTRACTS["S-007"]},
        )
        self.assertEqual(receipt["sources"][0]["source_id"], "S-007")

    def test_identical_shapes_from_two_sources_never_share_a_fingerprint(self):
        # Same observed shape, two different source_ids: the healthy path, the
        # empty-record path, the error-object path and the unparseable path all
        # have to stay distinguishable, because the receipt keys them by source
        # while a fingerprint read in isolation does not.
        rows = json.dumps(
            [{"項目": "x", "欄位名稱": "y", "數值": "1", "資料時間日期": "2026-09-01", "資料週期": "月", "地區": "北屯區"}]
        )
        healthy = [
            drift.observe(
                contract, rows, content_type="application/json", resource_id="r1"
            )["observed_schema_fingerprint"]
            for contract in (drift.CONTRACTS["S-028"], drift.CONTRACTS["CTX-POP"])
        ]
        self.assertEqual(len(set(healthy)), 2)

        record = {"proceedingsId": "p1", "billId": "b1", "date": "d", "speaker": "s", "content": "c"}
        council = json.dumps({"success": True, "data": {"data": [record], "totalPages": 1, "totalCount": 1}})
        api = [
            drift.observe(contract, council, content_type="application/json")["observed_schema_fingerprint"]
            for contract in (drift.CONTRACTS["S-007"], drift.CONTRACTS["S-009"])
        ]
        self.assertEqual(len(set(api)), 2)

        for body, content_type in (
            (b"not-json", "application/json"),
            (b'<html />', "text/html"),
            (b'{"error": "x"}', "application/json"),
        ):
            with self.subTest(body=body):
                broken = [
                    drift.observe(contract, body, content_type=content_type)["observed_schema_fingerprint"]
                    for contract in (drift.CONTRACTS["S-007"], drift.CONTRACTS["S-009"])
                ]
                self.assertEqual(len(set(broken)), 2)

    def test_empty_resources_are_unknown_not_complete_zero(self):
        result = drift.observe(drift.CONTRACTS["S-028"], "[]", content_type="application/json", resource_id="r1")
        self.assertEqual(result["status"], "CONTENT_SHAPE_UNKNOWN")
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertTrue(result["review_required"])

    def test_unparseable_or_wrong_content_requires_review_with_fingerprint(self):
        invalid_json = drift.observe(
            drift.CONTRACTS["S-007"], b"not-json", content_type="application/json"
        )
        self.assertEqual(invalid_json["status"], "CONTENT_SHAPE_UNKNOWN")
        self.assertTrue(invalid_json["review_required"])
        self.assertRegex(invalid_json["observed_schema_fingerprint"], r"^[0-9a-f]{64}$")

        wrong_content = drift.observe(
            drift.CONTRACTS["S-007"], b"<html />", content_type="text/html"
        )
        self.assertEqual(wrong_content["status"], "CONTENT_SHAPE_UNKNOWN")
        self.assertTrue(wrong_content["review_required"])
        self.assertIn("CONTENT_TYPE_MISMATCH", wrong_content["reasons"])

    def test_lkg_and_fingerprint_history_survive_a_break(self):
        good = drift.observe(drift.CONTRACTS["S-007"], json.dumps(API), content_type="application/json")
        state = drift.update_state(drift.empty_state(), good)
        broken = drift.observe(
            drift.CONTRACTS["S-007"], json.dumps({"error": "changed"}), content_type="application/json", previous=good
        )
        state = drift.update_state(state, broken)
        saved = state["sources"]["S-007"]
        self.assertEqual(saved["last_known_good"]["observed_schema_fingerprint"], good["observed_schema_fingerprint"])
        self.assertEqual(saved["current"]["status"], "BREAKING_DRIFT")
        self.assertEqual(saved["history"][0]["observed_schema_fingerprint"], good["observed_schema_fingerprint"])

    def test_source_unavailable_is_not_an_empty_success(self):
        result = drift.observe(
            drift.CONTRACTS["S-009"], b"", http_status=503, content_type="application/json"
        )
        self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(result["window_completeness"], "PARTIAL")

    def test_live_observations_cover_every_contract_and_fail_closed_per_source(self):
        response = SimpleNamespace(
            content=b"sample",
            status_code=200,
            headers={"content-type": "text/plain"},
            url="https://official.test/source",
            request=SimpleNamespace(url="https://official.test/source"),
        )

        def fetch(_session, source_id):
            if source_id == "S-009":
                raise RuntimeError("offline")
            return response, "resource-1"

        observations = drift.live_observations(session=object(), fetch_source=fetch)
        self.assertEqual({item["source_id"] for item in observations}, set(drift.CONTRACTS))
        failed = next(item for item in observations if item["source_id"] == "S-009")
        # No response was received, so the receipt must not attribute a status.
        self.assertEqual(failed["http_status"], 0)
        self.assertEqual(failed["error_reason"], "LIVE_FETCH_RUNTIMEERROR")
        observed = drift.observe(drift.CONTRACTS["S-009"], b"", http_status=failed["http_status"], error_reason=failed["error_reason"])
        self.assertEqual(observed["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(observed["reasons"], ["LIVE_FETCH_RUNTIMEERROR"])
        self.assertEqual(observed["http_status"], 0)
        self.assertTrue(observed["review_required"])

    def test_live_observations_retries_transient_connection_once(self):
        response = SimpleNamespace(
            content=b"sample",
            status_code=200,
            headers={"content-type": "text/plain"},
            url="https://official.test/source",
            request=SimpleNamespace(url="https://official.test/source"),
        )
        calls = {"S-001": 0}

        def fetch(_session, source_id):
            if source_id == "S-001":
                calls["S-001"] += 1
                if calls["S-001"] == 1:
                    raise ConnectionError("temporary network failure")
            return response, None

        with mock.patch.object(drift.time, "sleep") as sleep:
            observations = drift.live_observations(session=object(), fetch_source=fetch)
        self.assertEqual(calls["S-001"], 2)
        self.assertEqual(next(item for item in observations if item["source_id"] == "S-001")["http_status"], 200)
        sleep.assert_called_once_with(1)

    def test_interrupted_live_receipt_blocks_every_source_and_preserves_lkg(self):
        good = drift.observe(
            drift.CONTRACTS["S-001"],
            '<li><a href="news_view.jsp?dataserno=1">news 115-09-10</a></li>',
            content_type="text/html",
        )
        state = drift.update_state(drift.empty_state(), good)
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            output_path = Path(directory) / "receipt.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            output_path.write_text('{"overall":"HEALTHY"}', encoding="utf-8")
            with mock.patch.object(drift, "live_observations", side_effect=AssertionError("network called")):
                self.assertEqual(drift.main([
                    "--live-interrupted-receipt", "--state", str(state_path), "--output", str(output_path)
                ]), 0)
            receipt = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["overall"], "BLOCKED")
            self.assertEqual(len(receipt["sources"]), len(drift.CONTRACTS))
            for source in receipt["sources"]:
                self.assertEqual(source["status"], "SOURCE_UNAVAILABLE")
                self.assertEqual(source["reasons"], ["LIVE_COLLECTION_INTERRUPTED"])
                self.assertTrue(source["review_required"])
            s001 = next(source for source in receipt["sources"] if source["source_id"] == "S-001")
            self.assertEqual(s001["last_known_good"]["observed_schema_fingerprint"], good["observed_schema_fingerprint"])
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), state)

    def test_live_catalog_sources_use_bound_collector_transport(self):
        response = SimpleNamespace(content=b"sample", status_code=200, headers={}, url="https://official.test/source")
        with mock.patch("online_collect.get", return_value=response) as bounded_get:
            result, resource_id = drift._fetch_live_source(object(), "S-001")
        self.assertIs(result, response)
        self.assertIsNone(resource_id)
        bounded_get.assert_called_once_with(
            mock.ANY,
            online_collect.NEWS_LIST_SOURCES["S-001"]["list_url"],
            source_id="S-001",
        )

    def test_s001_uses_same_origin_fallback_after_transient_connection_error(self):
        response = SimpleNamespace(content=b"sample", status_code=200, headers={}, url="https://official.test/source")
        with mock.patch("online_collect.get", side_effect=[ConnectionError("reset"), response]) as bounded_get:
            result, resource_id = drift._fetch_live_source(object(), "S-001")
        self.assertIs(result, response)
        self.assertIsNone(resource_id)
        self.assertEqual(bounded_get.call_count, 2)
        self.assertEqual(bounded_get.call_args_list[1].args[1], online_collect.NEWS_LIST_SOURCES["S-001"]["fallback_list_url"])

    def test_receipt_rejects_duplicate_and_unknown_observation_ids(self):
        sample = {"source_id": "S-001", "body": b"<li><a href=\"news_view.jsp?dataserno=1\">news 115-09-10</a></li>"}
        with self.assertRaisesRegex(ValueError, "duplicate source_id"):
            drift.build_receipt([sample, dict(sample)])
        with self.assertRaisesRegex(ValueError, "unknown source_id"):
            drift.build_receipt([{**sample, "source_id": "S-UNKNOWN"}])


if __name__ == "__main__":
    unittest.main()
