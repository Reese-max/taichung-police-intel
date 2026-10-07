"""FICTIONAL_OFFLINE_ONLY fixtures; these tests provide no source licence or acquisition proof."""
import hashlib
import importlib.util
from pathlib import Path
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

if __name__ == '__main__': unittest.main()
