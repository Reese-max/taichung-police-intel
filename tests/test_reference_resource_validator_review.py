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

    def test_boolean_schema_version_rejected(self):
        body = b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n';plan=self.plan(body);plan['schema_version']=True
        with self.assertRaises(ValueError):m.validate(plan=plan,resource_bytes=body)

    def test_blank_data_rows_cannot_be_silently_removed(self):
        body=b'id,period,POINT_X,POINT_Y\na,fictional,121,24\n,,,\n'
        self.assertFalse(m.validate(plan=self.plan(body),resource_bytes=body)['completeness'])

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
