"""Synthetic checkpoint receipts; no network, publication, or rights changes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


outcome = load('bound_publication_outcome', 'publication-outcome.py')
checkpoint = load('bound_publication_checkpoint', 'publication-state-branch.py')


class PublicationDataReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name)
        self.health = outcome.load_system_health()
        self.policy = self.health.load_current_policy()
        self.status = {
            'generated_at': '2026-11-04T01:57:27+08:00',
            'latest_collection_run': {'collection_run_id': 'synthetic-generation', 'status': 'SUCCEEDED'},
            'sources': [{'source_id': source_id, 'source_health': 'PASS',
                         'freshness_status': 'FRESH', 'window_completeness': 'COMPLETE_ZERO',
                         'last_success_at': '2026-11-04T01:57:20+08:00'}
                        for source_id in self.policy['active_source_ids']],
        }
        self.brief = {'generated_at': self.status['generated_at'],
                      'source_status_generated_at': self.status['generated_at'],
                      'source_collection_run_id': 'synthetic-generation',
                      'publication_status': 'READY', 'snapshot_complete': True}
        self.env = {phase: 'success' for phase in outcome.PHASES}
        self.env.update(BUILD_RESULT='success', DEPLOY_RESULT='success',
                        QUERY_READINESS='RIGHTS_BLOCKED', QUERY_PRODUCTION_VERIFIED='false',
                        DATA_EVIDENCE_REQUIRED='true')
        self.write_bundle()

    def write_json(self, name, value):
        path = self.evidence / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(checkpoint.encoded(value))
        if name == 'apps/web/public/data/system-health.json':
            self.env['EXPECTED_SYSTEM_HEALTH_SHA256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def write_bundle(self):
        for name in checkpoint.STATE_PATHS:
            self.write_json(name, {})
        self.write_json('apps/web/public/data/source-status.json', self.status)
        self.write_json('apps/web/public/data/v2-daily-brief.json', self.brief)
        stages = self.health.current_publication_stages(self.status, self.brief, self.policy, {}, {}, {})
        self.system = self.health.build_health(stages)
        self.system['policy'] = self.health.policy_binding(self.policy)
        self.write_json('apps/web/public/data/system-health.json', self.system)
        self.env['GENERATION_ID'] = checkpoint.generation(checkpoint.read_working_bundle(self.evidence))

    def receipt(self, **overrides):
        return outcome.runtime_health({**self.env, **overrides}, data_evidence=self.evidence)

    def assert_unverified(self, reason):
        receipt = self.receipt()
        self.assertEqual(receipt['data_receipt']['verification'], 'UNVERIFIED')
        self.assertEqual(receipt['data_receipt']['reason'], reason)
        self.assertEqual(receipt['data_status'], 'UNKNOWN')
        self.assertNotEqual(receipt['lanes']['publication'], 'HEALTHY')
        self.assertEqual(receipt['deployment_status'], 'VERIFIED')
        return receipt

    def test_successful_deployment_retains_partial_and_source_gaps(self):
        self.status['latest_collection_run']['status'] = 'PARTIAL'
        self.status['sources'][0].update(window_completeness='PARTIAL', freshness_status='VERY_STALE')
        self.brief.update(publication_status='PARTIAL', snapshot_complete=False)
        self.write_bundle()
        receipt = self.receipt()
        self.assertEqual(receipt['data_status'], 'PARTIAL')
        self.assertEqual(receipt['lanes']['publication'], 'PARTIAL')
        self.assertEqual(receipt['overall'], 'PARTIAL')
        self.assertEqual(receipt['deployment_status'], 'VERIFIED')
        self.assertTrue(receipt['public_data_verified'])
        gap = receipt['data_receipt']['source_gaps'][0]
        self.assertEqual(gap['source_id'], self.status['sources'][0]['source_id'])
        self.assertIn('SOURCE_COVERAGE_OR_COLLECTION_GAP', gap['reasons'])
        self.assertIn('SOURCE_FRESHNESS_STALE', gap['reasons'])
        actions = receipt['operator_summary']['source_actions']
        self.assertEqual(actions[0]['source_id'], gap['source_id'])
        self.assertEqual(actions[0]['reasons'], gap['reasons'])
        self.assertTrue(actions[0]['next_actions'])
        self.assertFalse(receipt['production_verified'])
        self.assertEqual(receipt['query_readiness'], 'RIGHTS_BLOCKED')

    def test_complete_data_does_not_promote_formal_rights(self):
        receipt = self.receipt()
        self.assertEqual(receipt['data_status'], 'HEALTHY')
        self.assertEqual(receipt['lanes']['publication'], 'HEALTHY')
        self.assertEqual(receipt['data_receipt']['source_gaps'], [])
        self.assertFalse(receipt['production_verified'])
        self.assertEqual(receipt['query_readiness'], 'RIGHTS_BLOCKED')

    def test_replay_uses_bound_data_without_claiming_new_collection(self):
        self.status['sources'][0]['window_completeness'] = 'PARTIAL'
        self.write_bundle()
        receipt = self.receipt(COLLECT='skipped')
        self.assertEqual(receipt['data_status'], 'PARTIAL')
        self.assertEqual(receipt['phase_results']['COLLECT'], 'skipped')
        self.assertEqual(receipt['data_receipt']['collection_run_id'], 'synthetic-generation')
        self.assertEqual(receipt['data_receipt']['generation_id'], self.env['GENERATION_ID'])

    def test_data_failure_staleness_unknown_and_missing_source_are_not_healthy(self):
        original = copy.deepcopy(self.status)
        for change, expected in (('failed', 'BLOCKED'), ('stale', 'STALE'),
                                 ('unknown', 'UNKNOWN'), ('missing_source', 'PARTIAL')):
            with self.subTest(change=change):
                self.status = copy.deepcopy(original)
                if change == 'failed':
                    self.status['latest_collection_run']['status'] = 'FAILED'
                elif change == 'missing_source':
                    missing = self.status['sources'].pop()['source_id']
                else:
                    self.status['sources'][0]['freshness_status'] = 'STALE' if change == 'stale' else 'UNKNOWN'
                self.write_bundle()
                receipt = self.receipt()
                self.assertEqual(receipt['data_status'], expected)
                self.assertEqual(receipt['deployment_status'], 'VERIFIED')
                if change == 'missing_source':
                    self.assertIn(missing, [r['source_id'] for r in receipt['data_receipt']['source_gaps']])

    def test_partial_system_receipt_cannot_be_forged_as_success(self):
        self.status['sources'][0]['window_completeness'] = 'PARTIAL'
        self.write_bundle()
        row = next(r for r in self.system['stages'] if r['stage'] == 'collection')
        row['outcome'] = 'SUCCESS'
        self.write_json('apps/web/public/data/system-health.json', self.system)
        self.assert_unverified('DATA_RECEIPT_SYSTEM_MISMATCH')

    def test_required_evidence_cannot_be_omitted_by_library_caller(self):
        receipt = outcome.runtime_health(self.env)
        self.assertEqual(receipt['data_status'], 'UNKNOWN')
        self.assertEqual(receipt['data_receipt']['reason'], 'DATA_RECEIPT_MISSING')
        self.assertNotEqual(receipt['lanes']['publication'], 'HEALTHY')

    def test_any_checkpoint_bytes_change_invalidates_generation(self):
        for name in checkpoint.STATE_PATHS:
            with self.subTest(path=name):
                path = self.evidence / name
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                self.assert_unverified('DATA_RECEIPT_GENERATION_MISMATCH')
                path.write_bytes(original)

    def test_reused_collection_name_cannot_bind_an_old_generation(self):
        self.env['GENERATION_ID'] = 'a' * 64
        self.assert_unverified('DATA_RECEIPT_GENERATION_MISMATCH')

    def test_system_bytes_are_bound_to_the_build_output(self):
        path = self.evidence / 'apps/web/public/data/system-health.json'
        path.write_bytes(path.read_bytes() + b' ')
        self.assert_unverified('DATA_RECEIPT_SYSTEM_HASH_MISMATCH')
        self.env.pop('EXPECTED_SYSTEM_HEALTH_SHA256')
        self.assert_unverified('DATA_RECEIPT_SYSTEM_HASH_MISMATCH')

    def test_system_receipt_cannot_hide_or_alter_data(self):
        original = copy.deepcopy(self.system)
        for field, value in (('outcome', 'PARTIAL'), ('generation_id', 'older-run'),
                             ('source_warnings', []), ('policy_hash', 'a' * 64)):
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                row = next(row for row in changed['stages'] if row['stage'] == 'collection')
                # A no-op empty warnings mutation would not be a mismatch.
                row[field] = [{'source_id': 'fictional'}] if field == 'source_warnings' else value
                self.write_json('apps/web/public/data/system-health.json', changed)
                self.assert_unverified('DATA_RECEIPT_SYSTEM_MISMATCH')

    def test_missing_duplicate_malformed_and_wrong_policy_receipts_fail_closed(self):
        path = self.evidence / 'apps/web/public/data/system-health.json'
        path.unlink()
        self.assert_unverified('DATA_RECEIPT_INVALID_OR_MISSING')
        path.write_text('{', encoding='utf-8')
        self.env['EXPECTED_SYSTEM_HEALTH_SHA256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assert_unverified('DATA_RECEIPT_INVALID_OR_MISSING')
        bad = copy.deepcopy(self.system)
        bad['stages'].append(copy.deepcopy(next(r for r in bad['stages'] if r['stage'] == 'collection')))
        self.write_json('apps/web/public/data/system-health.json', bad)
        self.assert_unverified('DATA_RECEIPT_SYSTEM_MISMATCH')
        bad = copy.deepcopy(self.system)
        bad['policy']['policy_hash'] = 'b' * 64
        self.write_json('apps/web/public/data/system-health.json', bad)
        self.assert_unverified('DATA_RECEIPT_POLICY_MISMATCH')
        (self.evidence / checkpoint.STATE_PATHS[0]).unlink()
        self.assert_unverified('DATA_RECEIPT_INVALID_OR_MISSING')

    def test_good_data_receipt_does_not_override_ci_failures(self):
        for phase, stage in (('COLLECT', 'collection'), ('VERIFY', 'canonical_validation')):
            with self.subTest(phase=phase):
                receipt = self.receipt(**{phase: 'failure'})
                self.assertEqual(next(r for r in receipt['stages'] if r['stage'] == stage)['outcome'], 'FAILED')
                self.assertEqual(receipt['lanes']['publication'], 'BLOCKED')

    def test_cli_retains_health_and_failed_exit_when_required_artifact_missing(self):
        output = self.evidence / 'runtime-health.json'
        env = {**os.environ, **self.env}
        command = [sys.executable, '-X', 'utf8', str(ROOT / 'scripts/publication-outcome.py'),
                   '--health-output', str(output), '--data-evidence', str(self.evidence / 'missing')]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('data quality: `UNKNOWN`', result.stdout)
        self.assertEqual(json.loads(output.read_text())['deployment_status'], 'VERIFIED')

    def test_cli_reports_partial_separately_from_operational_success(self):
        self.status['sources'][0]['window_completeness'] = 'PARTIAL'
        self.write_bundle()
        output = self.evidence / 'runtime-health.json'
        result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/publication-outcome.py'),
                                 '--health-output', str(output), '--data-evidence', str(self.evidence)],
                                env={**os.environ, **self.env}, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Deployment: `VERIFIED`; data quality: `PARTIAL`', result.stdout)
        self.assertIn(self.status['sources'][0]['source_id'], result.stdout)
        self.assertFalse(json.loads(output.read_text())['production_verified'])

    def test_workflow_downloads_exact_producer_before_report_even_after_failure(self):
        workflow = (ROOT / '.github/workflows/pages.yml').read_text()
        job = workflow.split('  publication_outcome:', 1)[1]
        self.assertIn('name: publication-evidence-${{ github.run_id }}-${{ github.run_attempt }}', workflow.split('  publication_outcome:', 1)[0])
        self.assertIn('evidence_id: ${{ steps.evidence.outputs.artifact-id }}', workflow)
        self.assertIn('artifact-ids: ${{ needs.build.outputs.evidence_id }}', job)
        self.assertNotIn('github.run_attempt', job.split('name: Report actual', 1)[0])
        self.assertLess(job.index('uses: actions/download-artifact@'), job.index('name: Report actual'))
        self.assertIn('name: Report actual build and deployment outcomes\n        if: always()', job)
        self.assertIn('DATA_EVIDENCE_REQUIRED: "true"', job)
        self.assertIn('--data-evidence runtime-evidence/publication-data', job)
        self.assertIn('EXPECTED_SYSTEM_HEALTH_SHA256: ${{ needs.build.outputs.data_health_sha256 }}', job)
        self.assertIn('data_health_sha256: ${{ steps.data_receipt.outputs.sha256 }}', workflow)
        gate = (ROOT / 'scripts/verify-project.mjs').read_text()
        self.assertIn('"publication-data-receipt-tests"', gate)
        self.assertIn('"test_publication_data_receipt.py"', gate)


if __name__ == '__main__':
    unittest.main()
