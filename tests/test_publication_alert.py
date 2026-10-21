import copy
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('publication_alert', Path(__file__).resolve().parents[1] / 'scripts/publication-alert.py')
alert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alert)

spec = importlib.util.spec_from_file_location('publication_outcome', Path(__file__).resolve().parents[1] / 'scripts/publication-outcome.py')
outcome = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outcome)


class FakeGithub:
    def __init__(self):
        self.comments = []
        self.posts = 0
        self.corrupt_readback = False

    def __call__(self, path, payload=None):
        if payload is not None:
            self.posts += 1
            row = {'id': self.posts, 'body': payload['body'], 'user': {'login': 'github-actions[bot]'}}
            self.comments.append(row)
            return copy.deepcopy(row)
        if path.startswith('issues/20/comments?'):
            return copy.deepcopy(self.comments)
        row = copy.deepcopy(next(row for row in self.comments if str(row['id']) == path.rsplit('/', 1)[1]))
        if self.corrupt_readback:
            row['body'] += 'changed'
        return row


class PublicationAlertTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'GITHUB_REPOSITORY': alert.REPOSITORY,
            'GITHUB_ACTIONS': 'true', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.github = FakeGithub()
        self.health = {'kind': 'GOVINTEL_RUNTIME_HEALTH_RECEIPT', 'run_id': '123', 'run_attempt': '1',
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'phase_results': {key: 'success' for key in alert.PHASES},
            'job_results': {'BUILD_RESULT': 'success', 'DEPLOY_RESULT': 'success'},
            'public_data_verified': True, 'query_deployment_verified': True,
            'production_verified': False, 'query_readiness': 'RIGHTS_BLOCKED'}

    def send(self, health=None, scope='PRODUCTION_PUBLICATION'):
        return alert.deliver(health or self.health, scope=scope, request=self.github)

    def test_failure_is_delivered_read_back_and_deduplicated(self):
        self.health['phase_results']['PRESERVE'] = 'failure'
        self.health['raw_query'] = 'PRIVATE_QUERY_MUST_NOT_LEAVE'
        self.health['access_token'] = 'PRIVATE_TOKEN_MUST_NOT_LEAVE'
        first = self.send()
        second = self.send()
        self.assertEqual(first['status'], 'DELIVERED_READBACK_VERIFIED')
        self.assertEqual(first['failed_phases'], ['PRESERVE'])
        self.assertEqual(first['comment_id'], second['comment_id'])
        self.assertTrue(second['duplicate_prevented'])
        self.assertEqual(self.github.posts, 1)
        self.assertIsNone(first['human_acknowledged'])
        body = self.github.comments[0]['body']
        self.assertNotIn('PRIVATE_QUERY_MUST_NOT_LEAVE', body)
        self.assertNotIn('PRIVATE_TOKEN_MUST_NOT_LEAVE', body)

    def publication_health(self, **overrides):
        return outcome.runtime_health({
            **self.health['phase_results'], **self.health['job_results'],
            'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
            'STATE_COMMIT': 'a' * 40, 'GENERATION_ID': 'b' * 64,
            'EVIDENCE_URL': f'https://github.com/{alert.REPOSITORY}/actions/runs/123/artifacts/456',
            **overrides,
        })

    def test_failure_alert_locates_retained_evidence_and_exact_checkpoint(self):
        for phase in ('PRESERVE', 'PAGES_UPLOAD', 'PAGES_DEPLOY', 'PUBLIC_VERIFY'):
            with self.subTest(phase=phase):
                self.github = FakeGithub()
                health = self.publication_health(**{phase: 'failure'})
                receipt = self.send(health)
                body = self.github.comments[0]['body']
                self.assertEqual(receipt['failed_phases'], [phase])
                self.assertEqual(health.get('evidence_url'),
                    f'https://github.com/{alert.REPOSITORY}/actions/runs/123/artifacts/456')
                self.assertIn(health['evidence_url'], body)
                self.assertIn(health['state_commit'], body)
                self.assertIn(health['generation_id'], body)
                self.assertEqual(self.send(health)['comment_id'], receipt['comment_id'])
                self.assertEqual(self.github.posts, 1)

    def test_failed_evidence_upload_keeps_checkpoint_without_a_download_link(self):
        health = self.publication_health(PRESERVE='failure', EVIDENCE='failure')
        self.assertIsNone(health.get('evidence_url'))
        self.send(health)
        body = self.github.comments[0]['body']
        self.assertNotIn('/artifacts/', body)
        self.assertIn(health['state_commit'], body)
        self.assertIn('未取得成功上傳的 evidence artifact 連結', body)

    def test_untrusted_evidence_locations_never_reach_public_alerts(self):
        prefix = f'https://github.com/{alert.REPOSITORY}/actions/runs/123/artifacts/456'
        for url in (prefix + '?token=PRIVATE', prefix + '#PRIVATE',
                    prefix.replace('github.com/', 'PRIVATE@github.com/'),
                    prefix.replace(alert.REPOSITORY, 'foreign/repository'),
                    prefix.replace('/runs/123/', '/runs/999/'),
                    prefix + '> PRIVATE', 'https://evil.test/PRIVATE'):
            with self.subTest(url=url):
                self.github = FakeGithub()
                health = self.publication_health(PRESERVE='failure', EVIDENCE_URL=url)
                self.assertIsNone(health.get('evidence_url'))
                # The alert also validates receipt fields at its own trust boundary.
                health.update(evidence_url=url, state_commit='PRIVATE_STATE', generation_id='PRIVATE_GENERATION')
                self.send(health)
                body = self.github.comments[0]['body']
                self.assertNotIn('/artifacts/', body)
                self.assertNotIn('PRIVATE', body)

    def test_cancellation_and_contract_block_are_alerted(self):
        self.health['phase_results']['PAGES_DEPLOY'] = 'cancelled'
        self.health['schema_drift_overall'] = 'BLOCKED'
        self.assertEqual(self.send()['failed_phases'], ['PAGES_DEPLOY', 'SCHEMA_DRIFT'])

    def test_readback_corruption_fails_instead_of_claiming_delivery(self):
        self.health['phase_results']['PRESERVE'] = 'failure'
        self.github.corrupt_readback = True
        with self.assertRaisesRegex(ValueError, 'readback mismatch'):
            self.send()

    def test_workflow_repository_and_receipt_run_must_match_before_post(self):
        self.health['phase_results']['PRESERVE'] = 'failure'
        for key, value in [('GITHUB_REPOSITORY', 'foreign/repository'), ('GITHUB_RUN_ID', '999'),
                           ('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_ACTIONS', 'false')]:
            with self.subTest(key=key), mock.patch.dict(os.environ, {key:value}):
                with self.assertRaises(ValueError):
                    self.send()
        self.assertEqual(self.github.posts, 0)

    def test_rights_blocked_alone_does_not_create_a_failure_alert(self):
        self.assertEqual(self.send()['status'], 'NO_OPEN_ALERT')
        self.assertEqual(self.github.posts, 0)

    def test_production_recovery_requires_exact_public_and_gateway_verification(self):
        self.health['phase_results']['PRESERVE'] = 'failure'
        failed = self.send()
        self.health['phase_results']['PRESERVE'] = 'success'
        self.health['query_deployment_verified'] = False
        self.assertEqual(self.send()['status'], 'NO_INFRASTRUCTURE_RECOVERY_PROOF')
        self.health['query_deployment_verified'] = True
        recovery = self.send()
        self.assertEqual(recovery['mode'], 'RECOVERY')
        self.assertEqual(recovery['recovery_of_comment_id'], failed['comment_id'])
        self.assertIsNone(recovery['human_acknowledged'])

    def test_drill_and_production_alerts_do_not_close_each_other(self):
        self.health['phase_results']['PAGES_UPLOAD'] = 'failure'
        drill = self.send(scope='ISOLATED_ARTIFACT_UPLOAD_DRILL')
        self.health['phase_results']['PAGES_UPLOAD'] = 'success'
        self.assertEqual(self.send()['status'], 'NO_OPEN_ALERT')
        result = self.send(scope='ISOLATED_ARTIFACT_UPLOAD_DRILL')
        self.assertEqual(result['recovery_of_comment_id'], drill['comment_id'])

    def test_recovery_retry_reads_back_the_same_comment(self):
        self.health['phase_results']['PRESERVE'] = 'failure'
        self.send()
        self.health['phase_results']['PRESERVE'] = 'success'
        first = self.send()
        second = self.send()
        self.assertEqual(first['comment_id'], second['comment_id'])
        self.assertTrue(second['duplicate_prevented'])
        self.assertEqual(self.github.posts, 2)

    def test_verified_recovery_supersedes_older_failures_in_same_scope(self):
        self.health['phase_results']['PRESERVE'] = 'failure'
        self.send()
        self.health['run_id'] = '124'
        with mock.patch.dict(os.environ, {'GITHUB_RUN_ID': '124'}):
            latest = self.send()
            self.health['phase_results']['PRESERVE'] = 'success'
            recovery = self.send()
            self.assertEqual(recovery['recovery_of_comment_id'], latest['comment_id'])
        self.health['run_id'] = '125'
        with mock.patch.dict(os.environ, {'GITHUB_RUN_ID': '125'}):
            self.assertEqual(self.send()['status'], 'NO_OPEN_ALERT')
        self.assertEqual(self.github.posts, 3)


if __name__ == '__main__':
    unittest.main()
