"""FICTIONAL_OFFLINE_ONLY: contract regressions, not licences or official coverage."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location('json_collection_audit', Path(__file__).resolve().parents[1] / 'scripts/reference-json-collection-audit.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReferenceJsonCollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = 'S-028'
        self.declaration = {'inventory_id': self.source, 'http_status': 200,
                            'raw_sha256': 'a' * 64, 'resource_count': 0, 'resources': []}
        self.receipts = []

    def row(self, period='2026-01-01T00:00:00', cycle='月', value='42'):
        row = dict.fromkeys(m.FIELDS, 'FICTIONAL_PRIVATE_CONTACT_OR_LABEL')
        row.update({'資料時間日期': period, '資料週期': cycle, '數值': value})
        return row

    def resource(self, rows=None, *, body=None):
        rid = f'{len(self.receipts) + 1:08d}-0000-0000-0000-000000000000'
        url = 'https://fictional.invalid/resource?rid=' + rid
        if body is None:
            body = json.dumps(rows if rows is not None else [self.row()], ensure_ascii=False).encode()
        path = self.root / (self.source + '-' + rid + '-original.json')
        path.write_bytes(body)
        self.declaration['resources'].append({'resource_id': rid, 'resource_url': url, 'format': 'JSON', 'field_count': 13})
        self.declaration['resource_count'] += 1
        self.receipts.append({'source_id': self.source, 'resource_id': rid, 'requested_url': url,
                              'final_url': url, 'http_status': 200, 'bytes': len(body),
                              'sha256': hashlib.sha256(body).hexdigest()})
        return path

    def audit(self, include_index=False):
        return m.audit_collection(source_id=self.source, declaration=self.declaration,
                                  receipts=self.receipts, resource_root=self.root,
                                  include_disposition_index=include_index)

    def test_verified_declaration_does_not_promote_business_scope_or_rights(self):
        path = self.resource()
        before = path.read_bytes()
        result = self.audit()
        self.assertEqual(result['verified_resources'], 1)
        self.assertEqual(result['quality_status'], 'CONTRACT_CHECKS_PASS')
        self.assertEqual(result['business_scope_completeness'], 'NOT_VERIFIED')
        self.assertEqual(result['business_identity'], 'UNKNOWN')
        self.assertEqual(result['rights_review'], 'PENDING')
        self.assertFalse(result['production_active'])
        self.assertFalse(result['model_transmission_allowed'])
        self.assertEqual(result['source_timezone'], 'UNKNOWN')
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn('FICTIONAL_PRIVATE', json.dumps(result))

    def test_missing_receipt_blocks_collection_coverage(self):
        self.resource()
        self.receipts = []
        result = self.audit()
        self.assertEqual(result['quality_status'], 'RESOURCE_CONTRACT_BLOCKED')
        self.assertEqual(result['declaration_coverage'], 'PARTIAL')
        self.assertEqual(result['verified_resources'], 0)
        self.assertEqual(result['resources'][0]['reason_code'], 'DOWNLOAD_RECEIPT_ABSENT')

    def test_wrong_hash_or_byte_count_cannot_become_acquired(self):
        self.resource()
        self.receipts[0]['sha256'] = 'b' * 64
        self.assertEqual(self.audit()['verified_resources'], 0)
        self.receipts[0]['sha256'] = hashlib.sha256(next(self.root.iterdir()).read_bytes()).hexdigest()
        self.receipts[0]['bytes'] += 1
        self.assertEqual(self.audit()['resources'][0]['reason_code'], 'ORIGINAL_BYTES_OR_HASH_MISMATCH')

    def test_metadata_empty_duplicate_and_boolean_count_rejected(self):
        with self.assertRaises(ValueError): self.audit()
        self.resource()
        self.declaration['resource_count'] = True
        with self.assertRaises(ValueError): self.audit()
        self.declaration['resources'] *= 2
        self.declaration['resource_count'] = 2
        with self.assertRaises(ValueError): self.audit()

    def test_duplicate_or_unexpected_download_receipts_rejected(self):
        self.resource()
        self.receipts *= 2
        with self.assertRaises(ValueError): self.audit()
        self.receipts = self.receipts[:1]
        self.receipts[0]['resource_id'] = '99999999-0000-0000-0000-000000000000'
        with self.assertRaises(ValueError): self.audit()

    def test_source_url_and_redirect_mismatch_rejected(self):
        self.resource()
        self.receipts[0]['final_url'] += '&mirror=true'
        self.assertEqual(self.audit()['resources'][0]['reason_code'], 'DOWNLOAD_RECEIPT_CONTRACT_MISMATCH')

    def test_empty_html_schema_drift_and_duplicate_json_keys_rejected(self):
        for body in [b'[]', b'<html>fictional</html>', b'[{"a":1,"a":2}]', b'[{}]']:
            with self.subTest(body=body):
                self.declaration['resources'] = []
                self.declaration['resource_count'] = 0
                self.receipts = []
                self.resource(body=body)
                self.assertEqual(self.audit()['resources'][0]['reason_code'], 'JSON_DECODE_OR_SCHEMA_REJECTED')

    def test_actual_resource_missing_and_bounded_read_rejected(self):
        path = self.resource()
        path.unlink()
        self.assertEqual(self.audit()['resources'][0]['reason_code'], 'ORIGINAL_BYTES_ABSENT_OR_UNREADABLE')
        path.write_bytes(b'x' * (m.MAX_BYTES + 1))
        self.assertEqual(self.audit()['verified_resources'], 0)

    def test_schema_string_type_drift_is_not_silently_coerced(self):
        row = self.row()
        row['數值'] = 42
        self.resource([row])
        self.assertEqual(self.audit()['verified_resources'], 0)

    def test_collection_gap_does_not_become_world_zero_or_continuous_complete(self):
        self.resource([self.row('2026-04-01T00:00:00')])
        self.resource([self.row('2026-06-01T00:00:00')])
        result = self.audit()
        self.assertEqual(result['missing_observed_span_periods'], ['2026-05'])
        self.assertEqual(result['verified_resources'], 2)
        self.assertEqual(result['quality_status'], 'QUALITY_ISSUES')
        self.assertNotIn('2026-05', result['observed_period_rows'])
        self.assertIn('NOT_WORLD_ZERO', result['gap_scope'])

    def test_early_year_padding_does_not_invent_thousands_of_collection_gaps(self):
        self.resource([self.row('0001-01-01T00:00:00'), self.row('0001-02-01T00:00:00')])
        result = self.audit()
        self.assertEqual(result['observed_period_rows'], {'0001-01': 1, '0001-02': 1})
        self.assertEqual(result['missing_observed_span_periods'], [])
        self.source = 'CTX-POP'
        self.declaration['inventory_id'] = self.source
        self.declaration['resources'] = []
        self.declaration['resource_count'] = 0
        self.receipts = []
        self.resource([self.row('0001-01-01T00:00:00', cycle='年'), self.row('0003-01-01T00:00:00', cycle='年')])
        result = self.audit()
        self.assertEqual(result['observed_period_rows'], {'0001': 1, '0003': 1})
        self.assertEqual(result['missing_observed_span_periods'], ['0002'])

    def test_duplicates_keep_positions_multiplicity_and_contact_values_private(self):
        row = self.row()
        self.resource([row, row])
        self.resource([row])
        result = self.audit(True)
        self.assertEqual(result['data_rows'], 3)
        self.assertEqual(result['candidate_unreviewed_rows'], 3)
        self.assertEqual(result['candidate_unique_full_rows'], 1)
        self.assertEqual(result['duplicate_full_rows'], 2)
        self.assertEqual(result['cross_resource_exact_duplicate_rows'], 1)
        group = result['private_disposition_index']['duplicate_groups'][0]
        self.assertEqual(group['multiplicity'], 3)
        self.assertEqual(len(group['references']), 3)
        self.assertNotIn('FICTIONAL_PRIVATE', json.dumps(result))
        self.assertFalse(result['private_disposition_index']['model_transmission_allowed'])
        self.assertFalse(result['private_disposition_index']['row_hash_index_anonymization_approved'])

    def test_quarantine_can_have_multiple_reasons_and_invalid_dates_not_in_periods(self):
        self.resource([self.row('2026-02-30T00:00:00', cycle='年', value='')])
        result = self.audit(True)
        self.assertEqual(result['quarantined_rows'], 1)
        self.assertEqual(result['candidate_unreviewed_rows'], 0)
        self.assertEqual(result['observed_period_rows'], {})
        self.assertEqual(result['quarantine_reason_rows'], {'EMPTY_VALUE': 1, 'INVALID_COLLECTION_PERIOD': 1,
                                                           'UNEXPECTED_DECLARED_CYCLE': 1})

    def test_annual_duplicate_resource_is_flagged_without_doubling_population_claim(self):
        self.source = 'CTX-POP'
        self.declaration['inventory_id'] = self.source
        row = self.row('2023-01-01T00:00:00', cycle='年')
        self.resource([row])
        self.resource([row])
        result = self.audit()
        self.assertEqual(result['cross_resource_exact_duplicate_rows'], 1)
        self.assertEqual(len(result['identical_resource_groups']), 1)
        self.assertEqual(result['observed_period_rows'], {'2023': 2})
        self.assertEqual(result['business_scope_completeness'], 'NOT_VERIFIED')
        self.assertNotIn('population_total', result)

    def test_item_count_change_explains_rows_without_dropping_measures_or_granting_sum(self):
        first, second = [], []
        for item in ('FICTIONAL_FIRST_MEASURE', 'FICTIONAL_SECOND_MEASURE', 'FICTIONAL_THIRD_MEASURE'):
            for column in ('FICTIONAL_COLUMN_A', 'FICTIONAL_COLUMN_B'):
                row = self.row()
                row.update({'項目': item, '欄位名稱': column})
                first.append(row)
                if item == 'FICTIONAL_FIRST_MEASURE':
                    second.append(dict(row, 資料時間日期='2026-02-01T00:00:00'))
        self.resource(first)
        self.resource(second)
        result = self.audit()
        profile = result['candidate_grain_profile']
        self.assertEqual(result['data_rows'], 8)
        self.assertEqual(result['candidate_unreviewed_rows'], 8)
        self.assertEqual(result['duplicate_full_rows'], 0)
        self.assertEqual(profile['distinct_observed_item_sets'], 2)
        self.assertEqual(profile['distinct_observed_column_sets'], 1)
        self.assertTrue(profile['structural_variation_observed'])
        self.assertEqual([p['distinct_items'] for p in profile['periods']], [3, 1])
        self.assertEqual([p['item_column_count_histogram'] for p in profile['periods']], [{2: 3}, {2: 1}])
        self.assertTrue(all(p['all_items_share_same_column_set'] for p in profile['periods']))
        self.assertFalse(profile['periods'][1]['item_set_equals_first_observed_period'])
        self.assertEqual(result['aggregation_guard']['status'], 'NOT_ALLOWED')
        self.assertFalse(result['aggregation_guard']['within_period_aggregation_allowed'])
        self.assertFalse(result['aggregation_guard']['cross_period_aggregation_allowed'])
        self.assertFalse(result['aggregation_guard']['deduplication_allowed'])
        self.assertEqual(result['business_identity'], 'UNKNOWN')
        self.assertNotIn('FICTIONAL_FIRST_MEASURE', json.dumps(result))
        self.assertNotIn('FICTIONAL_COLUMN_A', json.dumps(result))

    def test_same_candidate_key_distinguishes_identical_rows_from_conflicting_values(self):
        row = self.row()
        self.resource([row, row, dict(row, 數值='43')])
        result = self.audit()
        profile = result['candidate_grain_profile']
        self.assertEqual(result['data_rows'], 3)
        self.assertEqual(result['duplicate_full_rows'], 1)
        self.assertEqual(profile['duplicate_candidate_key_rows'], 2)
        self.assertEqual(profile['conflicting_value_candidate_key_groups'], 1)
        self.assertEqual(profile['periods'][0]['candidate_key_multiplicity_histogram'], {3: 1})
        self.assertEqual(result['quality_status'], 'QUALITY_ISSUES')
        self.assertFalse(result['aggregation_guard']['deduplication_allowed'])

    def test_conflicting_key_without_exact_duplicates_is_quality_issue_and_not_numeric_coercion(self):
        row = self.row(value='42')
        self.resource([row, dict(row, 數值='42.0')])
        result = self.audit()
        self.assertEqual(result['duplicate_full_rows'], 0)
        self.assertEqual(result['candidate_grain_profile']['conflicting_value_candidate_key_groups'], 1)
        self.assertEqual(result['quality_status'], 'QUALITY_ISSUES')
        self.assertIn('NO_NUMERIC_COERCION', result['candidate_grain_profile']['value_comparison'])

    def test_candidate_grain_keeps_exact_dates_separate_from_month_grouping(self):
        # Direct helper contract: grouping for presentation must not remove a
        # declared key field, even when callers supply two dates in one month.
        candidates = {
            '2026-01': {
                ('2026-01-01T00:00:00', 'FICTIONAL_REGION', 'FICTIONAL_ITEM', 'FICTIONAL_COLUMN'): {'42': 1},
                ('2026-01-02T00:00:00', 'FICTIONAL_REGION', 'FICTIONAL_ITEM', 'FICTIONAL_COLUMN'): {'43': 1},
            },
        }
        profile = m.candidate_grain_profile(candidates)
        self.assertEqual(profile['candidate_key_fields'], list(m.CANDIDATE_KEY_FIELDS))
        self.assertEqual(profile['periods'][0]['candidate_rows'], 2)
        self.assertEqual(profile['periods'][0]['candidate_key_groups'], 2)
        self.assertEqual(profile['duplicate_candidate_key_rows'], 0)
        self.assertEqual(profile['conflicting_value_candidate_key_groups'], 0)
        self.assertEqual(profile['periods'][0]['candidate_key_multiplicity_histogram'], {1: 2})
        self.assertNotIn('2026-01-02', json.dumps(profile))

    def test_unicode_digits_quarantined_without_canonicalizing_into_same_key(self):
        for malformed in ('2026-01-0١T00:00:00', '２０２６-01-01T00:00:00', '2026-01-01T00:00:0٠'):
            with self.subTest(date=malformed):
                self.declaration['resources'] = []
                self.declaration['resource_count'] = 0
                self.receipts = []
                self.resource([self.row(value='42'), self.row(malformed, value='43')])
                result = self.audit()
                self.assertEqual(result['data_rows'], 2)
                self.assertEqual(result['candidate_unreviewed_rows'], 1)
                self.assertEqual(result['quarantined_rows'], 1)
                self.assertEqual(result['quarantine_reason_rows'], {'INVALID_COLLECTION_PERIOD': 1})
                self.assertEqual(result['observed_period_rows'], {'2026-01': 1})
                self.assertEqual(result['candidate_grain_profile']['periods'][0]['candidate_key_groups'], 1)
                self.assertEqual(result['candidate_grain_profile']['conflicting_value_candidate_key_groups'], 0)
                self.assertEqual(result['quality_status'], 'QUALITY_ISSUES')
                self.assertFalse(result['aggregation_guard']['within_period_aggregation_allowed'])
                self.assertNotIn(malformed, json.dumps(result))

    def test_cross_resource_candidate_duplicates_retain_provenance_and_quarantine_scope(self):
        self.source = 'CTX-POP'
        self.declaration['inventory_id'] = self.source
        row = self.row('2023-01-01T00:00:00', cycle='年')
        self.resource([row, dict(row, 數值='')])
        self.resource([row])
        result = self.audit()
        profile = result['candidate_grain_profile']
        self.assertEqual(result['data_rows'], 3)
        self.assertEqual(result['candidate_unreviewed_rows'], 2)
        self.assertEqual(result['quarantined_rows'], 1)
        self.assertEqual(result['cross_resource_exact_duplicate_rows'], 1)
        self.assertEqual(profile['periods'][0]['candidate_rows'], 2)
        self.assertEqual(profile['periods'][0]['candidate_key_groups'], 1)
        self.assertEqual(profile['duplicate_candidate_key_rows'], 1)
        self.assertEqual(profile['conflicting_value_candidate_key_groups'], 0)
        self.assertEqual(profile['periods'][0]['candidate_key_multiplicity_histogram'], {2: 1})
        self.assertEqual(len(result['resources']), 2)
        self.assertTrue(result['multiplicity_retained'])
        self.assertFalse(result['aggregation_guard']['deduplication_allowed'])

    def test_resource_symlink_escape_rejected(self):
        path = self.resource()
        path.unlink()
        path.symlink_to('/etc/hosts')
        with self.assertRaisesRegex(ValueError, 'escapes'): self.audit()

    def test_legacy_null_id_uses_existing_inventory_parser_and_conflicts_fail(self):
        self.resource()
        self.declaration['resources'][0]['resource_id'] = None
        self.assertEqual(self.audit()['verified_resources'], 1)
        self.declaration['resources'][0]['resource_id'] = '99999999-0000-0000-0000-000000000000'
        with self.assertRaisesRegex(ValueError, 'invalid/duplicate'): self.audit()

    def test_cli_hash_index_stays_private_and_refuses_original_directory_output(self):
        path = self.resource()
        before = path.read_bytes()
        with tempfile.TemporaryDirectory() as output_directory:
            metadata = Path(output_directory) / 'metadata.json'
            downloads = Path(output_directory) / 'downloads.json'
            metadata.write_text(json.dumps([self.declaration]))
            downloads.write_text(json.dumps(self.receipts))
            command = [sys.executable, str(Path(m.__file__)), '--source-id', self.source,
                       '--metadata-receipts', str(metadata), '--download-receipts', str(downloads),
                       '--resource-root', str(self.root)]
            rejected = subprocess.run(command + ['--out', str(path)], capture_output=True)
            self.assertEqual(rejected.returncode, 2)
            out, index = Path(output_directory) / 'aggregate.json', Path(output_directory) / 'private-index.json'
            accepted = subprocess.run(command + ['--out', str(out), '--private-index-out', str(index)], capture_output=True)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(index.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('private_disposition_index', json.loads(out.read_text()))
            self.assertNotIn('FICTIONAL_PRIVATE', out.read_text() + index.read_text())
            self.assertEqual(path.read_bytes(), before)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO regression')
    def test_fifo_raw_and_receipt_files_are_rejected_without_blocking_open(self):
        path = self.resource()
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(self.audit()['resources'][0]['reason_code'], 'ORIGINAL_BYTES_ABSENT_OR_UNREADABLE')
        with tempfile.TemporaryDirectory() as output_directory:
            fifo = Path(output_directory) / 'fictional-metadata-fifo'
            os.mkfifo(fifo)
            command = [sys.executable, str(Path(m.__file__)), '--source-id', self.source,
                       '--metadata-receipts', str(fifo), '--download-receipts', str(fifo),
                       '--resource-root', str(self.root)]
            result = subprocess.run(command, capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'regular file', result.stderr)
            metadata = Path(output_directory) / 'metadata.json'
            metadata.write_text(json.dumps([self.declaration]))
            command[command.index('--metadata-receipts') + 1] = str(metadata)
            result = subprocess.run(command, capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'regular file', result.stderr)


if __name__ == '__main__':
    unittest.main()
