"""Offline artifact registry models retained build outputs on partial reruns."""
import re
from pathlib import Path
import unittest
import tests.test_publication_data_receipt as fixture

ROOT = Path(__file__).resolve().parents[1]


class EvidenceProducerTests(unittest.TestCase):
    def make_bundle(self, collection):
        bundle = fixture.PublicationDataReceiptTests()
        bundle.setUp()
        self.addCleanup(bundle.doCleanups)
        bundle.status['latest_collection_run']['collection_run_id'] = collection
        bundle.status['sources'][0]['window_completeness'] = 'PARTIAL'
        bundle.brief['source_collection_run_id'] = collection
        bundle.write_bundle()
        return bundle

    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/pages.yml').read_text()
        self.download = re.search(r'      - name: Download[^\n]*\n(?:(?!      - name:).|\n)*',
                                  self.workflow.split('  publication_outcome:', 1)[1]).group()
        self.first = self.make_bundle('synthetic-build-attempt-1')
        self.second = self.make_bundle('synthetic-build-attempt-2')
        self.outputs = {'evidence': 'success', 'evidence_id': '101'}
        self.registry = {'101': self.first.evidence, '202': self.second.evidence,
                         'publication-evidence-900-1': self.first.evidence,
                         'publication-evidence-900-2': self.second.evidence}

    def retrieve(self, reporting_attempt, outputs=None):
        outputs = self.outputs if outputs is None else outputs
        # Execute only this small action-input contract, not a workflow or network.
        if '        if:' in self.download and (
                outputs.get('evidence') != 'success' or not outputs.get('evidence_id')):
            return None
        by_id = re.search(r'^          artifact-ids: (.+)$', self.download, re.M)
        if by_id:
            self.assertEqual(by_id[1], '${{ needs.build.outputs.evidence_id }}')
            return self.registry.get(outputs.get('evidence_id'))
        name = re.search(r'^          name: (.+)$', self.download, re.M)[1]
        name = name.replace('${{ github.run_id }}', '900').replace('${{ github.run_attempt }}', str(reporting_attempt))
        return self.registry.get(name)

    def test_first_attempt_uses_bound_receipts(self):
        receipt = fixture.outcome.runtime_health(self.first.env, data_evidence=self.retrieve(1))
        self.assertEqual(receipt['data_receipt']['verification'], 'VERIFIED')
        self.assertEqual(receipt['data_status'], 'PARTIAL')

    def test_downstream_only_reruns_keep_the_successful_build_artifact(self):
        for reporting_attempt in (2, 3, 50):
            with self.subTest(reporting_attempt=reporting_attempt):
                directory = self.retrieve(reporting_attempt)
                self.assertEqual(directory, self.first.evidence)
                receipt = fixture.outcome.runtime_health(self.first.env, data_evidence=directory)
                self.assertEqual(receipt['data_receipt']['verification'], 'VERIFIED')
                self.assertEqual(receipt['data_status'], 'PARTIAL')
                self.assertEqual(receipt['deployment_status'], 'VERIFIED')
                self.assertEqual(receipt['query_readiness'], 'RIGHTS_BLOCKED')
                self.assertFalse(receipt['production_verified'])

    def test_reporting_only_rerun_with_no_second_build_artifact(self):
        self.registry.pop('publication-evidence-900-2')
        directory = self.retrieve(2)
        self.assertEqual(directory, self.first.evidence)
        receipt = fixture.outcome.runtime_health(self.first.env, data_evidence=directory)
        self.assertEqual(receipt['data_receipt']['verification'], 'VERIFIED')
        self.assertEqual(receipt['data_status'], 'PARTIAL')

    def test_full_rerun_uses_new_build_output_not_earlier_artifact(self):
        outputs = {'evidence': 'success', 'evidence_id': '202'}
        directory = self.retrieve(3, outputs)
        self.assertEqual(directory, self.second.evidence)
        receipt = fixture.outcome.runtime_health(self.second.env, data_evidence=directory)
        self.assertEqual(receipt['data_receipt']['verification'], 'VERIFIED')
        self.assertEqual(receipt['data_receipt']['generation_id'], self.second.env['GENERATION_ID'])

    def test_old_artifact_cannot_pass_a_new_generation(self):
        receipt = fixture.outcome.runtime_health(self.second.env, data_evidence=self.retrieve(3))
        self.assertEqual(receipt['data_receipt']['verification'], 'UNVERIFIED')
        self.assertEqual(receipt['data_receipt']['reason'], 'DATA_RECEIPT_GENERATION_MISMATCH')
        self.assertEqual(receipt['data_status'], 'UNKNOWN')

    def test_missing_or_failed_producer_never_downloads_all_or_latest(self):
        self.assertIn("if: needs.build.outputs.evidence == 'success' && needs.build.outputs.evidence_id != ''", self.download)
        for outputs in ({'evidence': 'success', 'evidence_id': ''},
                        {'evidence': 'failure', 'evidence_id': '101'},
                        {'evidence': 'skipped', 'evidence_id': '101'}, {}):
            with self.subTest(outputs=outputs):
                self.assertIsNone(self.retrieve(2, outputs))
                receipt = fixture.outcome.runtime_health(self.first.env, data_evidence=None)
                self.assertEqual(receipt['data_status'], 'UNKNOWN')
                self.assertEqual(receipt['data_receipt']['verification'], 'UNVERIFIED')

    def test_workflow_exports_the_uploaded_id_and_keeps_hash_binding(self):
        self.assertIn('evidence_id: ${{ steps.evidence.outputs.artifact-id }}', self.workflow)
        self.assertNotIn('          name:', self.download)
        self.assertNotIn('github.run_attempt', self.download)
        self.assertNotIn('pattern:', self.download)
        self.assertNotIn('merge-multiple:', self.download)
        self.assertIn('EXPECTED_SYSTEM_HEALTH_SHA256: ${{ needs.build.outputs.data_health_sha256 }}', self.workflow)
        self.assertIn('GENERATION_ID: ${{ needs.build.outputs.generation_id }}', self.workflow)


if __name__ == '__main__':
    unittest.main()
