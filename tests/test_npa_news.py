"""Synthetic-only fixtures; never retain live names, contacts or case narratives."""
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from intel_v2 import npa_news as news

OBSERVED = "2026-10-05T23:00:00Z"
ROW = ["1", "合成測試公告", "合成測試機關", "2026-09-11T09:56", '第一行,"引號"\n第二行']


def csv_bytes(rows=None, headers=None, bom=False):
    output = io.StringIO(newline="")
    csv.writer(output).writerows((headers or [news.FIELDS, news.DESCRIPTION]) + (rows if rows is not None else [ROW]))
    return output.getvalue().encode("utf-8-sig" if bom else "utf-8")


class Response(io.BytesIO):
    status = 200
    def __init__(self, body, **headers):
        super().__init__(body)
        self.headers = {"Content-Type": "text/csv;charset=UTF-8", **headers}
    def geturl(self):
        return news.RESOURCE_URL


class Opener:
    def __init__(self, response):
        self.response, self.requests = response, []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


class NPANewsTests(unittest.TestCase):
    def collect(self, body=None, **kwargs):
        return news.collect_snapshot(body if body is not None else csv_bytes(), observed_at=OBSERVED, **kwargs)

    def test_row_budget_stops_csv_iteration_before_materializing_tail(self):
        yielded = []
        def rows():
            for index in range(news.MAX_ROWS + 4):
                yielded.append(index)
                yield news.FIELDS
            raise AssertionError("parser consumed over-budget tail")
        with patch.object(news.csv, "reader", return_value=rows()):
            with self.assertRaisesRegex(ValueError, "over-budget row count"):
                news.parse_csv(b"synthetic", observed_at=OBSERVED)
        self.assertEqual(len(yielded), news.MAX_ROWS + 3)

    def test_bom_quotes_newlines_chinese_description(self):
        receipt, state = self.collect(csv_bytes(bom=True))
        self.assertEqual(receipt['quality']['unique_records'], 1)
        self.assertEqual(receipt['quality']['description_rows'], 1)
        self.assertEqual(state['records']['1']['published_at'], '2026-09-11T09:56:00+08:00')
        self.assertEqual(state['records']['1']['publication_precision'], 'MINUTE')

    def test_description_before_header_or_absent(self):
        for headers in ([news.DESCRIPTION, news.FIELDS], [news.FIELDS]):
            self.collect(csv_bytes(headers=headers))

    def test_first_import_is_historical_baseline(self):
        receipt, state = self.collect()
        self.assertEqual(receipt['deltas'][0]['change_type'], 'HISTORICAL_BASELINE')
        self.assertEqual(state['records']['1']['first_seen_at'], '2026-10-05T23:00:00+00:00')
        self.assertIsNone(state['records']['1']['source_updated_at'])
        self.assertEqual(receipt['freshness_status'], 'UNKNOWN')
        self.assertFalse(receipt['activated'])
        self.assertEqual(receipt['rights_status'], 'UNKNOWN')
        self.assertEqual(receipt['public_records'], [])

    def test_repeat_revision_and_new_id_are_only_candidates(self):
        _, previous = self.collect()
        receipt, _ = self.collect(previous=previous)
        self.assertEqual(receipt['deltas'][0]['change_type'], 'UNCHANGED')
        revised = [*ROW[:4], '合成修訂內容']
        second = ['2', *ROW[1:]]
        receipt, _ = self.collect(csv_bytes([revised, second]), previous=previous)
        self.assertEqual([r['change_type'] for r in receipt['deltas']], ['CANDIDATE_REVISION', 'CANDIDATE_NEW_ID'])
        self.assertEqual(receipt['serial_identity_stability'], 'UNKNOWN')

    def test_absence_is_not_retraction_and_reappearance_not_new(self):
        _, initial = self.collect(csv_bytes([ROW, ['2', *ROW[1:]]]))
        receipt, reduced = self.collect(previous=initial)
        self.assertEqual(receipt['absent_serials'], ['2'])
        self.assertEqual(receipt['absence_semantics'], 'UNKNOWN_NOT_RETRACTION')
        receipt, _ = self.collect(csv_bytes([ROW, ['2', *ROW[1:]]]), previous=reduced)
        self.assertEqual(receipt['deltas'][1]['change_type'], 'UNCHANGED')

    def test_identical_and_conflicting_duplicates(self):
        receipt, _ = self.collect(csv_bytes([ROW, ROW]))
        self.assertEqual(receipt['quality']['identical_duplicate_rows'], 1)
        with self.assertRaisesRegex(ValueError, 'conflicting duplicate'):
            self.collect(csv_bytes([ROW, [*ROW[:4], '不同合成內容']]))

    def test_same_content_different_ids_not_merged(self):
        receipt, _ = self.collect(csv_bytes([ROW, ['2', *ROW[1:]]]))
        self.assertEqual(receipt['quality']['shared_content_hash_groups'], 1)
        self.assertEqual(receipt['quality']['unique_records'], 2)

    def test_no_personal_text_html_images_urls_escape(self):
        hostile = ['9', '合成姓名 A123456789', '合成機關', ROW[3], '<img src="https://example.invalid/private"> 合成電話 0900-000-000']
        receipt, state = self.collect(csv_bytes([hostile]))
        output = json.dumps([receipt, state], ensure_ascii=False)
        for text in (hostile[1], hostile[2], hostile[4], 'example.invalid', '0900-000-000'):
            self.assertNotIn(text, output)

    def test_date_only_precision(self):
        receipt, state = self.collect(csv_bytes([[*ROW[:3], '2026/09/11', ROW[4]]]))
        self.assertIsNone(state['records']['1']['published_at'])
        self.assertEqual(state['records']['1']['publication_precision'], 'DAY')

    def test_explicit_timezone_normalized(self):
        _, state = self.collect(csv_bytes([[*ROW[:3], '2026-09-11T01:56Z', ROW[4]]]))
        self.assertEqual(state['records']['1']['published_at'], '2026-09-11T09:56:00+08:00')

    def test_invalid_future_dates_and_observation(self):
        for value in ('2026-02-30', '2026-13-01', '115/09/11', '2026-10-07', '2026-10-06T08:00', 'yesterday'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.collect(csv_bytes([[*ROW[:3], value, ROW[4]]]))
        with self.assertRaises(ValueError):
            news.collect_snapshot(csv_bytes(), observed_at='2026-10-05T23:00:00')

    def test_taiwan_day_not_utc_day(self):
        news.collect_snapshot(csv_bytes([[*ROW[:3], '2026-10-06', ROW[4]]]), observed_at=OBSERVED)

    def test_schema_width_missing_values_and_bad_ids(self):
        for body in (csv_bytes(headers=[list(reversed(news.FIELDS))]), csv_bytes([ROW + ['extra']]),
                     csv_bytes([ROW[:-1]]), csv_bytes([['https://example.invalid', *ROW[1:]]]),
                     csv_bytes([[ROW[0], '', *ROW[2:]]]), csv_bytes([], headers=[news.FIELDS])):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.collect(body)

    def test_encoding_malformed_and_byte_row_budget(self):
        for body in (b'', b'\xff', csv_bytes() + b'\0', b'"unclosed', b'x' * (news.MAX_BYTES + 1)):
            with self.assertRaises(ValueError):
                self.collect(body)
        with patch.object(news, 'MAX_ROWS', 1), self.assertRaises(ValueError):
            self.collect(csv_bytes([ROW, ['2', *ROW[1:]]]))

    def test_previous_state_identity_and_untrusted_fields_fail_closed(self):
        _, state = self.collect()
        for mutate in (lambda s: s.update(source_id='S-001'), lambda s: s.update(records={}),
                       lambda s: s['records']['1'].update(content='must not propagate'),
                       lambda s: s['records']['1'].update(record_sha256='broken'),
                       lambda s: s.update(observed_at='2027-01-01T00:00:00Z')):
            bad = copy.deepcopy(state)
            mutate(bad)
            with self.assertRaises(ValueError):
                self.collect(previous=bad)

    def test_failure_does_not_mutate_previous_state(self):
        _, state = self.collect()
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            self.collect(b'wrong schema', previous=state)
        self.assertEqual(state, before)

    def test_live_fetch_exact_one_get_hash_and_limits(self):
        body = csv_bytes()
        opener = Opener(Response(body, **{'Content-Length': str(len(body))}))
        fetched, receipt = news.fetch_csv(opener=opener)
        self.assertEqual(fetched, body)
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, news.RESOURCE_URL)
        self.assertEqual(timeout, 20)
        self.assertEqual(receipt['mode'], 'BOUNDED_PUBLIC_FETCH')

    def test_live_rejects_wrong_status_url_mime_encoding_length(self):
        responses = [Response(csv_bytes(), **headers) for headers in (
            {'Content-Type': 'text/html'}, {'Content-Encoding': 'gzip'},
            {'Content-Length': str(news.MAX_BYTES + 1)}, {'Content-Length': 'oops'}, {'Content-Length': '2'})]
        wrong_status = Response(csv_bytes()); wrong_status.status = 503
        wrong_url = Response(csv_bytes()); wrong_url.geturl = lambda: 'https://example.invalid/'
        for response in responses + [wrong_status, wrong_url]:
            with self.assertRaises(ValueError):
                news.fetch_csv(opener=Opener(response))

    def test_live_stream_byte_and_time_budget(self):
        with patch.object(news, 'MAX_BYTES', 10), self.assertRaises(ValueError):
            news.fetch_csv(opener=Opener(Response(b'x' * 11)))
        times = iter([0, 31])
        with self.assertRaisesRegex(ValueError, 'time budget'):
            news.fetch_csv(opener=Opener(Response(csv_bytes())), clock=lambda: next(times))

    def test_redirect_is_rejected_before_following(self):
        with self.assertRaisesRegex(ValueError, 'redirects'):
            news._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid')

    def test_cli_requires_explicit_input_or_live(self):
        process = subprocess.run([sys.executable, 'scripts/npa-news-candidate.py'], capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('required', process.stderr)


if __name__ == '__main__':
    unittest.main()
