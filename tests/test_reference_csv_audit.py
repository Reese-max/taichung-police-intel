"""FICTIONAL_OFFLINE_ONLY fixtures; these tests provide no source licence or acquisition proof."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('reference_csv_audit', Path(__file__).resolve().parents[1] / 'scripts/reference-csv-audit.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ReferenceCsvAuditTests(unittest.TestCase):
    def run_audit(self, content, source='S-036'):
        body = content.encode()
        return m.audit(body, source_id=source, expected_sha256=hashlib.sha256(body).hexdigest())

    def assembly(self, rows, *, description=True):
        labels = ','.join(m.ASSEMBLY_LABELS) + '\n' if description else ''
        return ','.join(m.HEADERS['S-036']) + '\n' + labels + rows

    def test_exact_description_only_skipped(self):
        result = self.run_audit(self.assembly('2026/10/01 10:00,2026/10/01 11:00,fictional,fictional,fictional\n'))
        self.assertEqual(result['data_rows'], 1)
        self.assertEqual(result['description_rows'], 1)
        self.assertFalse(result['production_active'])
        self.assertEqual(result['source_timezone'], 'UNKNOWN')
        self.assertEqual(result['business_scope_completeness'], 'NOT_VERIFIED')

    def test_assembly_requires_one_exact_description_row_immediately_after_header(self):
        row = '2026/10/01 10:00,2026/10/01 11:00,fictional,fictional,fictional\n'
        labels = ','.join(m.ASSEMBLY_LABELS) + '\n'
        changed_labels = ','.join([*m.ASSEMBLY_LABELS[:-1], 'fictional-label']) + '\n'
        invalid_rows = {
            'missing': row,
            'changed': changed_labels + row,
            'misplaced': row + labels,
            'duplicated': labels + labels + row,
        }
        for case, rows in invalid_rows.items():
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, 'description row'):
                    self.run_audit(self.assembly(rows, description=False))

    def test_hash_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            m.audit(b'fictional', source_id='S-036', expected_sha256='0' * 64)

    def test_html_and_ragged_fail_closed(self):
        for text in ['<html>denied</html>', self.assembly('one,two\n')]:
            with self.assertRaises(ValueError): self.run_audit(text)

    def test_duplicates_reversed_intervals_and_empty_cells_are_not_success(self):
        row = '2026/10/01 12:00,2026/10/01 11:00,fictional,,fictional\n'
        result = self.run_audit(self.assembly(row + row))
        self.assertEqual(result['duplicate_full_data_rows'], 1)
        self.assertEqual(result['end_before_start_count'], 2)
        self.assertEqual(result['empty_cells'], 2)
        self.assertEqual(result['quality_status'], 'QUALITY_ISSUES')

    def test_invalid_calendar_and_time_shape_are_reported(self):
        result = self.run_audit(self.assembly('2026/02/30 10:00,2026/03/01 11:00,fictional,fictional,fictional\n'))
        self.assertEqual(result['invalid_time_rows'], 1)
        self.assertIsNone(result['first_start'])

    def test_valid_roc_period_does_not_become_local_case_count(self):
        text = ','.join(m.HEADERS['CTX-165']) + '\n11509,fictional.invalid,fictional,fictional,fictional\n'
        result = self.run_audit(text, 'CTX-165')
        self.assertEqual(result['observed_roc_periods'], ['11509'])
        self.assertFalse(result['local_case_count_allowed'])
        self.assertEqual(result['domain_identity_validation'], 'NOT_RUN')

    def test_invalid_roc_month_reported(self):
        text = ','.join(m.HEADERS['CTX-165']) + '\n11513,fictional.invalid,fictional,fictional,fictional\n'
        self.assertEqual(self.run_audit(text, 'CTX-165')['invalid_period_rows'], 1)

    def test_empty_and_oversized_rejected(self):
        old = m.MAX_BYTES
        m.MAX_BYTES = 1
        try:
            for body in [b'', b'aa']:
                with self.assertRaises(ValueError):
                    m.audit(body, source_id='S-036', expected_sha256=hashlib.sha256(body).hexdigest())
        finally: m.MAX_BYTES = old

    def test_quarantine_is_per_row_and_multiplicity_is_retained(self):
        good = '2026/10/01 10:00,2026/10/01 11:00,FICTIONAL_PRIVATE_CATEGORY,FICTIONAL_PRIVATE_PLACE,fictional\n'
        bad = '2026/10/01 12:00,2026/10/01 11:00,fictional,,fictional\n'
        body = self.assembly(good + good + bad).encode()
        result = m.audit(body, source_id='S-036', expected_sha256=hashlib.sha256(body).hexdigest(),
                         include_disposition_index=True)
        self.assertEqual(result['candidate_unreviewed_rows'], 2)
        self.assertEqual(result['candidate_unique_full_rows'], 1)
        self.assertEqual(result['quarantined_rows'], 1)
        self.assertEqual(result['quarantine_reason_rows'], {'EMPTY_REQUIRED_VALUE': 1, 'END_BEFORE_START': 1})
        self.assertEqual(result['private_disposition_index']['duplicate_groups'][0]['csv_record_numbers'], [3, 4])
        self.assertEqual(result['private_disposition_index']['duplicate_groups'][0]['multiplicity'], 2)
        self.assertNotIn('FICTIONAL_PRIVATE', json.dumps(result))
        self.assertFalse(result['model_transmission_allowed'])
        self.assertFalse(result['row_hash_index_anonymization_approved'])
        self.assertEqual(result['business_identity'], 'UNKNOWN')

    def test_hash_index_record_ordinals_survive_description_and_quoted_newline(self):
        row = '2026/10/01 10:00,2026/10/01 11:00,fictional,"fictional\nplace",fictional\n'
        body = self.assembly(row + row).encode()
        result = m.audit(body, source_id='S-036', expected_sha256=hashlib.sha256(body).hexdigest(),
                         include_disposition_index=True)
        index = result['private_disposition_index']
        self.assertEqual([r['csv_record_number'] for r in index['rows']], [3, 4])
        self.assertEqual(index['rows'][0]['row_sha256'], index['rows'][1]['row_sha256'])
        self.assertEqual(result['source_timezone'], 'UNKNOWN')

    def test_default_report_has_no_row_level_index_and_does_not_delete_duplicates(self):
        row = '11509,FICTIONAL_PRIVATE.invalid,fictional,fictional,fictional\n'
        result = self.run_audit(','.join(m.HEADERS['CTX-165']) + '\n' + row + row, 'CTX-165')
        self.assertEqual(result['data_rows'], 2)
        self.assertEqual(result['candidate_unreviewed_rows'], 2)
        self.assertNotIn('private_disposition_index', result)
        self.assertNotIn('FICTIONAL_PRIVATE', json.dumps(result))

    def test_cli_rejects_original_as_output_and_preserves_original_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'original.csv'
            body = self.assembly('2026/10/01 10:00,2026/10/01 11:00,fictional,fictional,fictional\n').encode()
            path.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            command = [sys.executable, str(Path(m.__file__)), '--source-id', 'S-036', '--resource', str(path),
                       '--expected-sha256', sha]
            rejected = subprocess.run(command + ['--out', str(path)], capture_output=True)
            self.assertEqual(rejected.returncode, 2)
            out, index = Path(directory) / 'summary.json', Path(directory) / 'index.json'
            accepted = subprocess.run(command + ['--out', str(out), '--private-index-out', str(index)], capture_output=True)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sha)
            self.assertEqual(index.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('private_disposition_index', json.loads(out.read_text()))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO regression')
    def test_fifo_resource_is_rejected_without_blocking_open(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / 'fictional-original-fifo'
            os.mkfifo(fifo)
            command = [sys.executable, str(Path(m.__file__)), '--source-id', 'S-036',
                       '--resource', str(fifo), '--expected-sha256', 'a' * 64]
            result = subprocess.run(command, capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'regular file', result.stderr)

if __name__ == '__main__': unittest.main()
