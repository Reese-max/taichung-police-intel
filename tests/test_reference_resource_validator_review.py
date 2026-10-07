"""Independent review regressions; all plan/CSV fixtures FICTIONAL_OFFLINE_ONLY."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('reference_validator_review', Path(__file__).resolve().parents[1] / 'scripts/reference-resource-validator.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class IndependentValidatorReviewTests(unittest.TestCase):
    def plan(self, body):
        return {'schema_version': 1, 'source_id': 'D2', 'fixture_kind': 'FICTIONAL',
                'declared_sha256': hashlib.sha256(body).hexdigest(), 'schema': ['id','period','POINT_X','POINT_Y'],
                'id_fields': ['id'], 'expected_row_count': 1, 'period_evidence': {'field':'period','value':'fictional'},
                'coordinate_fields': ['POINT_X','POINT_Y'], 'acquisition': {'status':'LOCAL_BYTES','http_status':200}}

    def test_positive_fixture_is_only_a_local_plan_contract(self):
        body=b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n'
        receipt=m.validate(plan=self.plan(body),resource_bytes=body)
        self.assertTrue(receipt['completeness'])
        self.assertEqual(receipt['validation_scope'],'LOCAL_SUPPLIED_PLAN_CONTRACT_ONLY')
        self.assertEqual(receipt['business_scope_completeness'],'NOT_VERIFIED')
        self.assertEqual(receipt['rights_decision'],'PENDING')
        self.assertFalse(receipt['promotion'])

    def test_absent_resource_with_metadata_200_is_not_zero_rows(self):
        plan=self.plan(b'fictional');plan['declared_sha256']=None;plan['expected_row_count']=None
        plan['metadata']={'http_status':200,'sha256':'0'*64};plan['acquisition']={'status':'BLOCKED','http_status':403}
        receipt=m.validate(plan=plan,resource_bytes=None)
        self.assertEqual(receipt['acquisition'],'BLOCKED')
        self.assertIsNone(receipt['resource_rows'])
        self.assertFalse(receipt['completeness'])
        self.assertEqual(receipt['coordinate_validation'],'NOT_RUN')

    def test_duplicate_missing_ids_and_period_drift_fail(self):
        for rows in ['a,fictional,121,24\na,fictional,121,24\n', ',fictional,121,24\n', 'a,other,121,24\n']:
            body=('id,period,POINT_X,POINT_Y\n'+rows).encode()
            plan=self.plan(body);plan['expected_row_count']=len(rows.splitlines())
            self.assertFalse(m.validate(plan=plan,resource_bytes=body)['completeness'])

    def test_zero_rows_never_prove_period_or_completeness(self):
        body = b'id,period,POINT_X,POINT_Y\n'
        plan = self.plan(body); plan['expected_row_count'] = 0
        receipt = m.validate(plan=plan, resource_bytes=body)
        self.assertFalse(receipt['completeness'])
        self.assertFalse(receipt['period_ok'])

    def test_local_error_body_with_403_cannot_be_original_acquisition(self):
        body = b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n'
        plan = self.plan(body); plan['acquisition']['http_status'] = 403
        receipt = m.validate(plan=plan, resource_bytes=body)
        self.assertFalse(receipt['original_bytes_acquired'])
        self.assertFalse(receipt['completeness'])

    def test_coordinate_failure_invalidates_supplied_plan_contract(self):
        body = b'id,period,POINT_X,POINT_Y\na,fictional,999,24\n'
        plan = self.plan(body); plan['crs'] = {'epsg':4326,'units':'degree','axis_order':'lon-lat','authority':'AUTHORITATIVE_METADATA'}
        plan['coordinate_validation_requested'] = True
        receipt = m.validate(plan=plan, resource_bytes=body)
        self.assertFalse(receipt['completeness'])

    def test_plan_literal_authority_cannot_prove_official_crs(self):
        body = b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n'
        plan = self.plan(body); plan['crs'] = {'epsg':4326,'units':'degree','axis_order':'lon-lat','authority':'AUTHORITATIVE_METADATA'}
        receipt = m.validate(plan=plan, resource_bytes=body)
        self.assertEqual(receipt['coordinate_reference_system'], 'UNKNOWN')
        self.assertEqual(receipt['crs_authenticity'], 'NOT_VERIFIED')

    def test_json_rejected_even_when_caller_schema_matches_csv_interpretation(self):
        body = b'["id"]\n["fictional"]\n'
        plan = {'schema_version':1,'source_id':'S-028','fixture_kind':'FICTIONAL','declared_sha256':hashlib.sha256(body).hexdigest(),
                'schema':['["id"]'],'id_fields':['["id"]'],'expected_row_count':1,'period_evidence':{'field':'["id"]','value':'["fictional"]'}}
        self.assertFalse(m.validate(plan=plan,resource_bytes=body)['completeness'])

    def test_zip_magic_rejected_even_when_caller_schema_and_rows_match(self):
        # FICTIONAL signature-bearing bytes, not an acquired archive. Everything
        # after the ZIP local-file signature deliberately satisfies the CSV plan.
        body = b'PK\x03\x04id,period\nfictional-id,fictional\n'
        plan = self.plan(body)
        plan.update(schema=['PK\x03\x04id', 'period'],
                    id_fields=['PK\x03\x04id'], coordinate_fields=[])
        receipt = m.validate(plan=plan, resource_bytes=body)
        self.assertFalse(receipt['completeness'], receipt)
        self.assertIn('NON_CSV_HTML_OR_ERROR_PAYLOAD', receipt['completeness_failures'])
        self.assertFalse(receipt['original_bytes_acquired'])
        self.assertIsNone(receipt['resource_rows'])

    def test_utf8_bom_json_rejected_even_when_caller_schema_and_rows_match(self):
        # These are valid JSON documents. The caller can otherwise reinterpret
        # each closing delimiter as one CSV row and satisfy its own period plan.
        for opening, closing in [('[', ']'), ('{', '}')]:
            with self.subTest(opening=opening):
                body = b'\xef\xbb\xbf' + f'{opening}\n{closing}\n'.encode('utf-8')
                plan = self.plan(body)
                plan.update(schema=[opening], id_fields=[opening], coordinate_fields=[],
                            period_evidence={'field': opening, 'value': closing})
                receipt = m.validate(plan=plan, resource_bytes=body)
                self.assertFalse(receipt['completeness'], receipt)
                self.assertIn('NON_CSV_HTML_OR_ERROR_PAYLOAD', receipt['completeness_failures'])
                self.assertFalse(receipt['original_bytes_acquired'])
                self.assertIsNone(receipt['resource_rows'])

    def test_boolean_schema_version_rejected(self):
        body = b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n';plan=self.plan(body);plan['schema_version']=True
        with self.assertRaises(ValueError):m.validate(plan=plan,resource_bytes=body)

    def test_blank_data_rows_cannot_be_silently_removed(self):
        body=b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n,,,\n'
        self.assertFalse(m.validate(plan=self.plan(body),resource_bytes=body)['completeness'])

    def test_empty_logical_records_cannot_be_silently_removed(self):
        header = 'id,period,POINT_X,POINT_Y'
        first = 'a,fictional,121,24'
        second = 'b,fictional,121,24'
        for newline in ['\n', '\r\n']:
            for position, records, expected_rows in [
                ('after-header', [header, '', first], 1),
                ('between-data', [header, first, '', second], 2),
                ('after-data', [header, first, ''], 1),
            ]:
                with self.subTest(newline=repr(newline), position=position):
                    body = (newline.join(records) + newline).encode('utf-8')
                    plan = self.plan(body)
                    # Match the rows left after DictReader discards []. Merely
                    # matching this caller declaration must not hide corruption.
                    plan['expected_row_count'] = expected_rows
                    receipt = m.validate(plan=plan, resource_bytes=body)
                    self.assertFalse(receipt['completeness'], receipt)
                    self.assertTrue(receipt['completeness_failures'])

    def test_normal_line_endings_and_one_terminal_newline_remain_valid(self):
        for newline in ['\n', '\r\n']:
            for terminal_newline in ['', newline]:
                with self.subTest(newline=repr(newline), terminal=bool(terminal_newline)):
                    body = ('id,period,POINT_X,POINT_Y' + newline
                            + 'a,fictional,121,24' + terminal_newline).encode('utf-8')
                    receipt = m.validate(plan=self.plan(body), resource_bytes=body)
                    self.assertTrue(receipt['completeness'], receipt)
                    self.assertEqual(receipt['resource_rows'], 1)

    def test_blank_physical_line_inside_quoted_multiline_cell_remains_valid(self):
        for newline in ['\n', '\r\n']:
            with self.subTest(newline=repr(newline)):
                body = (f'id,period,POINT_X,POINT_Y,notes{newline}'
                        f'a,fictional,121,24,"fictional first{newline}'
                        f'{newline}fictional last"{newline}').encode('utf-8')
                plan = self.plan(body)
                plan['schema'] = [*plan['schema'], 'notes']
                receipt = m.validate(plan=plan, resource_bytes=body)
                self.assertTrue(receipt['completeness'], receipt)
                self.assertEqual(receipt['resource_rows'], 1)

    def test_unclosed_quote_rejected(self):
        body=b'id,period,POINT_X,POINT_Y\n"a,fictional,121,24\n'
        self.assertFalse(m.validate(plan=self.plan(body),resource_bytes=body)['completeness'])

    def test_bound_is_enforced_at_file_read(self):
        class BoundedPath:
            def read_bytes(self):raise AssertionError('must not read entire resource')
            def open(self, mode):return __import__('io').BytesIO(b'x'*(m.DEFAULT_MAX_BYTES+1))
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'plan.json';p.write_text(__import__('json').dumps(self.plan(b'fictional')))
            receipt=m.validate_files(p,BoundedPath())
        self.assertIn('PAYLOAD_TOO_LARGE',receipt['completeness_failures'])

if __name__ == '__main__': unittest.main()
