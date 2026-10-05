"""Actual persisted-generation restart/HTTP regressions for issue #30."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from threading import Thread
from unittest import mock
import unittest
from urllib.request import Request, urlopen

from governed_policy_fixture import make_governed_policy_fixture, load_fixture_module

ROOT = Path(__file__).resolve().parents[1]
g = None  # Each test imports the current runtime from its isolated governed fixture.


class GenerationFallbackTests(unittest.TestCase):
    def setUp(self):
        global g
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        temporary = Path(self.tmp.name)
        self.fixture = make_governed_policy_fixture(
            temporary / 'governed', source_root=ROOT, rights_reviewed=True, brief_reviewed=True,
        )
        g = load_fixture_module(self.fixture, 'generation_gateway_test', 'scripts/query-gateway.py')
        # The subprocess uses this same copied current runtime and policy, rather
        # than the real repository's unreviewed legacy publication.
        shutil.copyfile(ROOT / 'scripts/query-gateway-stdio.py',
                        self.fixture['root'] / 'scripts/query-gateway-stdio.py')
        self.root = temporary / 'cache-inputs'
        self.root.mkdir()
        self.cache = self.root / 'last-good.json'
        self.paths = {}
        self.initial_bytes = {}
        for name in ('FEED', 'STATUS', 'BRIEF'):
            original = getattr(g.qs, 'DEFAULT_' + name)
            path = self.root / original.name
            path.write_bytes(original.read_bytes())
            self.paths[name] = path
            self.initial_bytes[name] = original.read_bytes()
            patch = mock.patch.object(g.qs, 'DEFAULT_' + name, path)
            patch.start()
            self.addCleanup(patch.stop)

    def load(self):
        return g.load_snapshot(last_good_path=self.cache)

    def advance(self, broken=False):
        run = 'CR-SYNTHETIC-REBUILD-B'
        for name in ('FEED', 'STATUS', 'BRIEF'):
            path = self.paths[name]
            data = json.loads(path.read_text())
            if name == 'FEED':
                data['collection_run_id'] = run
                if broken:
                    data['items'][0].pop('stable_id')
            elif name == 'STATUS':
                data['latest_collection_run']['collection_run_id'] = run
            else:
                data['source_collection_run_id'] = run
            path.write_text(json.dumps(data, ensure_ascii=False))

    def request(self, base, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(base + path, data=data, headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def test_failed_rebuild_survives_restart_and_http_mcp_keep_one_stale_generation(self):
        first = self.load()
        old_bytes = self.cache.read_bytes()
        self.advance(broken=True)
        restarted = self.load()
        self.assertEqual(restarted['store'], first['store'])
        self.assertEqual(self.cache.read_bytes(), old_bytes)
        gateway = g.QueryGateway(restarted)
        server = g.build_server('127.0.0.1', 0, gateway)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            payload = {'tool': 'search_evidence', 'arguments': {'limit': 1}}
            web = self.request(base, '/query', payload)
            rpc = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'search_evidence', 'arguments': {'limit': 1}}}
            mcp = self.request(base, '/mcp', rpc)['result']['structuredContent']
            self.assertEqual(web['query_generation_id'], first['store']['generation_id'])
            self.assertEqual(web['query_generation_id'], mcp['query_generation_id'])
            self.assertEqual(web['results'], mcp['results'])
            self.assertEqual(web['query_index_status'], 'STALE_INDEX')
            self.assertEqual(web['freshness'], 'STALE')
            self.assertFalse(web['query_coverage']['can_state_bounded_no_match'])
            self.assertEqual(self.request(base, '/health')['status'], 'degraded')
            missing = gateway.execute('search_evidence', {'q': 'absent-synthetic-keyword'})
            self.assertFalse(missing['answerable_no_match'])
            self.assertFalse(gateway.execute('get_current_brief', {})['current_as_of_server_clock'])
            self.assertFalse(gateway.execute('get_publication_receipt', {})['publication_receipt']['current_as_of_server_clock'])
            with self.assertRaisesRegex((g.GatewayError, ValueError), 'generation'):
                gateway.execute('search_evidence', {'expected_generation': '0' * 64})
            with self.assertRaisesRegex(g.GatewayError, 'retained|rebuild|historical'):
                gateway.execute('validate_answer', {'claims': [{'text': 'Current', 'claim_type': 'OTHER'}]})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_valid_next_generation_atomically_recovers_and_is_replayable(self):
        first = self.load()
        self.assertEqual(self.fixture['policy']['schema_version'], 2)
        self.assertEqual(first['store']['counts']['publication_items'], 2)
        self.assertEqual(first['store']['formal_admission']['status'], 'ADMITTED')
        self.advance()
        next_snapshot = self.load()
        self.assertNotEqual(first['store']['generation_id'], next_snapshot['store']['generation_id'])
        self.assertNotIn('rebuild_failure', next_snapshot)
        self.assertEqual(self.load()['store'], next_snapshot['store'])
        gateway = g.QueryGateway(next_snapshot)
        with self.assertRaisesRegex((g.GatewayError, ValueError), 'generation'):
            gateway.execute('search_evidence', {'expected_generation': first['store']['generation_id']})

    def test_atomic_cache_write_failure_retains_old_bytes_and_old_generation(self):
        first = self.load()
        old_bytes = self.cache.read_bytes()
        self.advance()
        with mock.patch.object(g.os, 'replace', side_effect=OSError('synthetic disk full')):
            snapshot = self.load()
        self.assertEqual(snapshot['store'], first['store'])
        self.assertEqual(self.cache.read_bytes(), old_bytes)
        self.assertEqual(snapshot['rebuild_failure']['error_class'], 'OSError')
        self.assertEqual(set(self.root.iterdir()), set(self.paths.values()) | {self.cache, self.cache.with_name(self.cache.name + '.lock')})

    @unittest.skipIf(os.name == 'nt', 'POSIX directory fsync ordering')
    def test_success_flushes_file_then_rename_then_parent_directory(self):
        operations = []
        fsync = g.os.fsync
        replace = g.os.replace
        def record_fsync(fd):
            operations.append('directory-fsync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file-fsync')
            fsync(fd)
        def record_replace(source, target):
            operations.append('rename')
            replace(source, target)
        with mock.patch.object(g.os, 'fsync', side_effect=record_fsync), mock.patch.object(g.os, 'replace', side_effect=record_replace):
            snapshot = self.load()
        self.assertEqual(operations, ['file-fsync', 'rename', 'directory-fsync'])
        self.assertEqual(g._cached_snapshot(self.cache)['store'], snapshot['store'])
        self.assertEqual(stat.S_IMODE(self.cache.stat().st_mode), 0o600)

    def test_concurrent_rebuild_cannot_overwrite_a_locked_generation(self):
        first = self.load()
        old_bytes = self.cache.read_bytes()
        self.advance()
        with g._generation_lock(self.cache):
            snapshot = self.load()
        self.assertEqual(snapshot['store'], first['store'])
        self.assertEqual(self.cache.read_bytes(), old_bytes)
        self.assertIn('rebuild_failure', snapshot)

    def test_real_stdio_process_restart_preserves_cache_and_marks_failure(self):
        cache = self.root / 'stdio-cache.json'
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'search_evidence', 'arguments': {'limit': 1}}}
        command = [sys.executable, str(self.fixture['root'] / 'scripts/query-gateway-stdio.py'), '--last-good-snapshot', str(cache)]
        first = subprocess.run(command, input=json.dumps(request) + '\n', text=True, capture_output=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        old = json.loads(first.stdout)['result']['structuredContent']
        old_bytes = cache.read_bytes()
        failed = subprocess.run(command + ['--query-store', str(self.root / 'missing-generation.json')],
                                input=json.dumps(request) + '\n', text=True, capture_output=True, timeout=10)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        retained = json.loads(failed.stdout)['result']['structuredContent']
        self.assertEqual(retained['query_generation_id'], old['query_generation_id'])
        self.assertEqual(retained['results'], old['results'])
        self.assertEqual(retained['query_index_status'], 'STALE_INDEX')
        self.assertEqual(cache.read_bytes(), old_bytes)

    def test_missing_or_tampered_cache_never_licenses_fallback(self):
        self.advance(broken=True)
        with self.assertRaises((ValueError, OSError)):
            self.load()
        # A good cache remains hash-bound, even when an attacker re-seals the wrapper.
        for name in ('FEED', 'STATUS', 'BRIEF'):
            self.paths[name].write_bytes(self.initial_bytes[name])
        self.load()
        cache = json.loads(self.cache.read_text())
        cache['snapshot']['store']['items'][0]['title'] = 'forged title'
        cache['snapshot_sha256'] = g._json_hash(cache['snapshot'])
        self.cache.write_text(json.dumps(cache))
        self.advance(broken=True)
        with self.assertRaisesRegex(ValueError, 'canonical|cache|snapshot'):
            self.load()

    def test_cache_cannot_overwrite_canonical_input(self):
        old_bytes = self.paths['FEED'].read_bytes()
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            g.load_snapshot(last_good_path=self.paths['FEED'])
        self.assertEqual(self.paths['FEED'].read_bytes(), old_bytes)

    def test_changed_policy_refuses_even_a_hash_valid_historical_cache(self):
        self.load()
        self.advance(broken=True)
        policy = g.qs.load_current_policy()
        policy['policy_hash'] = '0' * 64
        with mock.patch.object(g.qs, 'load_current_policy', return_value=policy):
            with self.assertRaisesRegex(ValueError, 'canonical|cache'):
                self.load()


if __name__ == '__main__':
    unittest.main()
