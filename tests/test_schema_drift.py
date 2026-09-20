from __future__ import annotations

import copy
import json
import unittest

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


class SchemaDriftTests(unittest.TestCase):
    def test_all_required_news_contracts_use_real_parser_shapes(self):
        html = {
            "S-001": '<li><a href="news_view.jsp?dataserno=1">警政新聞 115-09-10</a></li>',
            "S-019": '<li><a href="/12047/12142/12186/873338/post">會議紀錄 115-09-10</a></li>',
            "S-032": '<li><a href="index.asp?Parser=9,4,632">交通消息 115-09-10</a></li>',
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

    def test_empty_resources_are_unknown_not_complete_zero(self):
        result = drift.observe(drift.CONTRACTS["S-028"], "[]", content_type="application/json", resource_id="r1")
        self.assertEqual(result["status"], "CONTENT_SHAPE_UNKNOWN")
        self.assertEqual(result["window_completeness"], "PARTIAL")

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


if __name__ == "__main__":
    unittest.main()
