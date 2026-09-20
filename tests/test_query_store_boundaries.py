import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/query-store.py'
spec = importlib.util.spec_from_file_location('query_store_boundary', SCRIPT)
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)


class QueryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.feed, fh = qs.load_json(qs.DEFAULT_FEED)
        self.status, sh = qs.load_json(qs.DEFAULT_STATUS)
        self.brief, bh = qs.load_json(qs.DEFAULT_BRIEF)
        for source in self.status["sources"]:
            source["freshness_status"] = "FRESH"
        self.hashes = {'feed': fh, 'status': sh, 'brief': bh}
        self.now = max(qs.instant(d['generated_at']) for d in (self.feed, self.status, self.brief)) + timedelta(minutes=1)

    def build(self):
        return qs.build_store(self.feed, self.status, self.brief, self.hashes)

    def test_mixed_collection_runs_rejected(self):
        self.status['latest_collection_run']['collection_run_id'] = 'different-run'
        with self.assertRaisesRegex(ValueError, 'cross-generation'):
            self.build()

    def test_same_run_different_generation_timestamp_rejected(self):
        self.brief['source_status_generated_at'] = self.now.isoformat()
        with self.assertRaisesRegex(ValueError, 'cross-generation'):
            self.build()

    def test_non_object_rows_never_silently_omitted(self):
        for field, target in (('items', self.feed), ('sources', self.status)):
            with self.subTest(field=field):
                target[field].append(None)
                with self.assertRaisesRegex(ValueError, 'invalid .*row'):
                    self.build()
                target[field].pop()

    def test_missing_expected_source_rejected(self):
        self.status['sources'].pop()
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.build()

    def test_arbitrary_replacement_source_rejected(self):
        self.status['sources'][0]['source_id'] = 'FAKE-SOURCE'
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.build()

    def test_feed_unknown_source_rejected(self):
        self.feed['items'][0]['source_id'] = 'S-032'
        with self.assertRaisesRegex(ValueError, 'unknown source'):
            self.build()

    def test_invalid_types_and_credentials_are_not_evidence(self):
        original = copy.deepcopy(self.feed['items'][0])
        for key, val in [('evidence_count', True), ('evidence_count', '1'), ('official_url', 'https:///bad'),
                         ('official_url', 'https://user:secret@official.test/')]:
            with self.subTest(key=key, val=val):
                self.feed['items'][0] = {**original, key: val}
                with self.assertRaises(ValueError):
                    self.build()

    def test_no_result_retains_source_failure_and_not_reassuring(self):
        self.status['sources'][0]['source_health'] = 'FAILED'
        result = qs.query_store(self.build(), text='not-in-this-archive', now=self.now)
        self.assertEqual(result['result_count'], 0)
        self.assertEqual(result['data_status'], 'PARTIAL')
        self.assertFalse(result['answerable_no_match'])
        self.assertTrue(result['source_gaps'])

    def test_complete_zero_is_bounded_to_snapshot_not_world(self):
        result = qs.query_store(self.build(), text='not-in-this-archive', now=self.now)
        self.assertEqual(result['data_status'], 'SNAPSHOT_RECENT')
        self.assertTrue(result['answerable_no_match'])
        self.assertFalse(result['publication_deployment_verified'])
        self.assertIn('not all real-world events', result['answer_scope'])

    def test_old_ready_data_remains_stale_at_query_time(self):
        result = qs.query_store(self.build(), text='not-in-this-archive', now=self.now + timedelta(days=30))
        self.assertEqual(result['data_status'], 'STALE')
        self.assertFalse(result['answerable_no_match'])

    def test_stale_source_data_cannot_be_reassured_as_empty(self):
        for freshness in ('STALE', 'VERY_STALE'):
            with self.subTest(freshness=freshness):
                self.status['sources'][0]['freshness_status'] = freshness
                result = qs.query_store(self.build(), text='not-in-this-archive', now=self.now)
                self.assertEqual(result['data_status'], 'STALE')
                self.assertFalse(result['answerable_no_match'])
                self.assertIn('STALE_SOURCE_DATA', {gap['reason'] for gap in result['source_gaps']})

    def test_missing_or_no_data_freshness_cannot_be_reassured_as_empty(self):
        for freshness in (None, 'NO_DATA', 'UNKNOWN'):
            with self.subTest(freshness=freshness):
                if freshness is None:
                    self.status['sources'][0].pop('freshness_status', None)
                else:
                    self.status['sources'][0]['freshness_status'] = freshness
                result = qs.query_store(self.build(), text='not-in-this-archive', now=self.now)
                self.assertEqual(result['data_status'], 'UNKNOWN')
                self.assertFalse(result['answerable_no_match'])
                self.assertIn('UNKNOWN_SOURCE_FRESHNESS', {gap['reason'] for gap in result['source_gaps']})

    def test_unsupported_source_is_not_a_complete_empty_answer(self):
        result = qs.query_store(self.build(), source_id='S-032', now=self.now)
        self.assertEqual(result['data_status'], 'SOURCE_NOT_AVAILABLE')
        self.assertFalse(result['answerable_no_match'])

    def test_missing_source_check_time_propagates_unknown(self):
        self.status['sources'][0]['last_checked_at'] = None
        result = qs.query_store(self.build(), now=self.now)
        self.assertEqual(result['data_status'], 'UNKNOWN')

    def test_limit_is_strict_integer(self):
        for limit in [True, 1.5, '5', None, -1, 101]:
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'limit'):
                qs.query_store(self.build(), limit=limit)

    def test_search_input_budget_and_generation_pin(self):
        for kwargs in [{'text': 'a'*513}, {'text': ['bad']}, {'change_type': 'RANDOM'}, {'expected_generation': 'wrong'}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                qs.query_store(self.build(), **kwargs)

    def test_index_tamper_rejected(self):
        store = self.build()
        store['items'][0]['title'] = 'modified without rebuild'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            qs.query_store(store)

    def test_cursor_pagination_keeps_ids_and_snapshot_without_duplicates(self):
        store = self.build()
        ids, cursor = [], None
        while True:
            page = qs.query_store(store, limit=7, cursor=cursor, now=self.now)
            ids.extend(row['canonical_id'] for row in page['results'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(len(ids), len(store['items']))
        self.assertEqual(len(ids), len(set(ids)))

    def test_cursor_cannot_cross_generation_or_filters(self):
        store = self.build()
        cursor = qs.query_store(store, limit=1, now=self.now)['next_cursor']
        self.assertIsNotNone(cursor)
        with self.assertRaisesRegex(ValueError, 'cursor'):
            qs.query_store(store, cursor=cursor, source_id='S-004')
        self.hashes['brief'] = 'b'*64
        newer = self.build()
        with self.assertRaisesRegex(ValueError, 'cursor'):
            qs.query_store(newer, cursor=cursor)

    def test_timezone_order_is_by_instant_not_lexicographic_text(self):
        row = copy.deepcopy(self.feed['items'][0])
        self.feed['items'] = [{**row, 'stable_id': 'early', 'published_at': '2026-09-17T09:00:00+08:00'},
                              {**row, 'stable_id': 'later', 'published_at': '2026-09-17T02:00:00+00:00'}]
        result = qs.query_store(self.build(), now=self.now)
        self.assertEqual([r['canonical_id'] for r in result['results']], ['later', 'early'])

    def test_failed_index_swap_preserves_previous_store_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'index.json'
            qs.atomic_write_json(output, self.build())
            before = output.read_bytes()
            self.hashes['brief'] = 'b'*64
            with mock.patch.object(qs.os, 'replace', side_effect=OSError('injected disk fault')):
                with self.assertRaises(OSError):
                    qs.atomic_write_json(output, self.build())
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(Path(tmp).iterdir()), [output])

    def test_actual_cli_build_then_query_stale_checked_in_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'store.json'
            build = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), 'build', '--output', str(output)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(build.returncode, 0, build.stderr)
            query = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), 'query', '--store', str(output), '--q', '警察', '--limit', '2'],
                                   capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(query.returncode, 0, query.stderr)
            result = json.loads(query.stdout)
            self.assertLessEqual(result['result_count'], 2)
            self.assertFalse(result['publication_deployment_verified'])
            self.assertEqual(result['search_scope'], 'TITLE_COMMITTEE_SOURCE_ID_ONLY')
            self.assertTrue(result['canonical_artifact_hashes'])


if __name__ == '__main__':
    unittest.main()
