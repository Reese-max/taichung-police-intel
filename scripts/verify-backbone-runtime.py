#!/usr/bin/env python3
"""Replay pinned implementations in isolation; never merge, deploy or modify policy.

The cross-component flow is synthetic, with literal test expectations. The separate
query scenario uses checked-in real publication metadata. Neither is production or
human-labelled accuracy evidence. All outputs state this distinction explicitly.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'config/backbone-runtime.v1.json'


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def execute(args, cwd, timeout=120):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location('backbone_' + name, path)
    if spec is None or spec.loader is None:
        raise ValueError('module not found: ' + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def expect_error(operation, label):
    try:
        operation()
    except ValueError:
        return
    raise AssertionError(label)


def prepare(manifest, components):
    require(manifest.get('schema_version') == 1, 'manifest version')
    require(manifest.get('repository') == 'Reese-max/taichung-police-intel', 'repository allowlist')
    require(len({c['name'] for c in manifest['components']}) == len(manifest['components']), 'duplicate component')
    for component in manifest['components']:
        name, commit = component['name'], component['sha']
        require(re.fullmatch('[a-z]+', name) and re.fullmatch('[0-9a-f]{40}', commit), 'invalid checkout identity')
        path = components / name
        if not path.exists():
            fetched = execute(['git', 'fetch', '--no-tags', '--depth=1', 'origin', commit], ROOT)
            require(fetched.returncode == 0, 'failed pinned fetch: ' + name + '\n' + fetched.stderr)
            added = execute(['git', 'worktree', 'add', '--detach', str(path), commit], ROOT)
            require(added.returncode == 0, 'failed isolated worktree: ' + name + '\n' + added.stderr)
        actual = execute(['git', 'rev-parse', 'HEAD'], path)
        require(actual.returncode == 0 and actual.stdout.strip() == commit, 'checkout SHA mismatch: ' + name)
        dirty = execute(['git', 'status', '--porcelain', '--untracked-files=no'], path)
        require(dirty.returncode == 0 and not dirty.stdout.strip(), 'dirty tracked checkout: ' + name)


def component_suites(manifest, components, output):
    records = []
    for component in manifest['components']:
        record = {**component, 'validation_scope': 'COMPONENT_RUNTIME_TESTS', 'suites': []}
        for number, pattern in enumerate(component['suites']):
            require(re.fullmatch(r'test_[A-Za-z0-9_*]+\.py', pattern), 'invalid suite pattern')
            start = time.monotonic()
            result = execute([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'], components / component['name'])
            text = result.stdout + '\n' + result.stderr
            log = f"{component['name']}-{number}.log"
            (output / log).write_text(text, encoding='utf-8')
            count = re.search(r'Ran (\d+) tests?', text)
            record['suites'].append({'pattern': pattern, 'exit_code': result.returncode,
                                    'test_count': int(count[1]) if count else None,
                                    'elapsed_seconds': round(time.monotonic() - start, 3),
                                    'log': log, 'log_sha256': hashlib.sha256(text.encode()).hexdigest()})
            write_json(output / 'components.json', records + [record])
            require(result.returncode == 0 and count and int(count[1]) > 0,
                    'component suite failed or zero tests: ' + component['name'] + '\n' + text)
        record['status'] = 'PASS'
        records.append(record)
    return records


def integration(components, output):
    query = load_module('query', components / 'query/scripts/query-store.py')
    entity = load_module('entity', components / 'entity/scripts/entity-registry.py')
    fusion = load_module('fusion', components / 'fusion/scripts/public-event-fusion.py')
    answer = load_module('answer', components / 'answer/scripts/answer-evidence-gate.py')
    health = load_module('health', components / 'health/scripts/system-health.py')
    evaluator = load_module('evaluation', components / 'evaluation/scripts/evaluate-govintel.py')

    # Real checked-in metadata, not live data. The query core must not confer deployment verification.
    store = query.build_from_paths(query.DEFAULT_FEED, query.DEFAULT_STATUS, query.DEFAULT_BRIEF)
    selected = query.query_store(store, source_id='S-004', limit=5)
    require(selected['query_generation_id'] == store['generation_id'], 'query generation drift')
    require(all(r['source_id'] == 'S-004' for r in selected['results']), 'query scope drift')
    require(selected['publication_deployment_verified'] is False, 'index laundered deployment status')
    require('source_gaps' in selected, 'query lost source gaps')
    write_json(output / 'real-snapshot-query.json', selected)

    registry = entity.load_registry()
    police = entity.resolve(registry, 'agency', '中市警', '臺中市')
    require(police.get('entity_id') == 'agency:tc-police', 'police alias resolution')
    xitun = next(r for r in registry['entities'] if r['entity_id'] == 'location:tc-xitun')
    district = entity.resolve(registry, 'location', xitun['canonical_label'], xitun['jurisdiction'])
    require(district.get('entity_id') == 'location:tc-xitun', 'district resolution')
    agency_ids = []
    for agency_id in ('agency:tc-news', 'agency:tc-police', 'agency:tc-traffic'):
        row = next(r for r in registry['entities'] if r['entity_id'] == agency_id)
        resolved = entity.resolve(registry, 'agency', row['canonical_label'], row['jurisdiction'])
        require(resolved.get('entity_id') == agency_id, 'agency resolution')
        agency_ids.append(resolved['entity_id'])

    # All events/URLs below are explicitly SYNTHETIC and never fetched or published.
    documents = [
        {'document_id': f'fixture:doc:{i}', 'document_version_id': f'fixture:doc:{i}:v1',
         'source_id': f'FIXTURE-{i}', 'independent_source_id': f'FIXTURE-OFFICIAL-{i}',
         'authority': 'official', 'title': f'合成測試公告 {i}', 'event_type': 'large_event',
         'named_event_id': 'fixture:occurrence:2026-09-20',
         'event_start_at': '2026-09-20T17:00:00+08:00', 'event_end_at': '2026-09-20T22:00:00+08:00',
         'district_id': district['entity_id'], 'location_ids': [district['entity_id']],
         'agency_ids': [agency_id], 'official_url': f'https://fixture.example.test/agency-{i}/doc'}
        for i, agency_id in enumerate(agency_ids)
    ]
    baseline = fusion.fuse_documents(documents)
    require(len(baseline) == 1 and baseline[0]['fusion_status'] == 'CONFIRMED', 'three-source fusion failed')
    require(baseline[0]['independent_source_count'] == 3, 'independent count')
    changed = copy.deepcopy(documents)
    changed[1]['event_start_at'] = '2026-09-20T16:00:00+08:00'
    changed[1]['document_version_id'] = 'fixture:doc:1:v2'
    conflict = fusion.fuse_documents(changed)
    require(len(conflict) == 1 and conflict[0]['fusion_status'] == 'CONFLICT', 'official conflict erased')
    require(conflict[0]['public_event_id'] == baseline[0]['public_event_id'], 'same-day version changed identity')
    reconciled_docs = copy.deepcopy(changed)
    for i, document in enumerate(reconciled_docs):
        document['event_start_at'] = '2026-09-20T16:00:00+08:00'
        document['document_version_id'] = f'fixture:doc:{i}:v2'
    reconciled = fusion.fuse_documents(reconciled_docs)
    require(reconciled[0]['fusion_status'] == 'CONFIRMED', 'reconciled event not confirmed')
    require(reconciled[0]['public_event_id'] == baseline[0]['public_event_id'], 'reconciled identity drift')
    other_day = copy.deepcopy(documents[0])
    other_day.update(document_id='fixture:other-day', document_version_id='fixture:other-day:v1',
                     event_start_at='2026-09-21T17:00:00+08:00', event_end_at='2026-09-21T22:00:00+08:00')
    split = fusion.fuse_documents([documents[0], other_day])
    require(len(split) == 2, 'different days silently merged')

    def evidence_catalog(docs):
        publication_hash = sha(docs)
        return {'schema_version': 1, 'publication_hash': publication_hash, 'evidence': [
            {'evidence_id': d['document_version_id'], 'publication_hash': publication_hash,
             'source_id': d['source_id'], 'source_document_version': d['document_version_id'],
             'content_sha256': sha(d), 'locator': d['official_url'], 'trust_tier': 'OFFICIAL',
             'verification_status': 'VERIFIED', 'freshness': 'RECENT',
             'facts': {'event_start_at': d['event_start_at'], 'district_id': d['district_id']}}
            for d in docs]}

    catalog = evidence_catalog(reconciled_docs)
    claim = {'claim_id': 'fixture:time', 'current_claim': True,
             'required_facts': [{'field': 'event_start_at', 'value': '2026-09-20T16:00:00+08:00'}],
             'evidence_ids': [d['document_version_id'] for d in reconciled_docs]}
    payload = {'publication_hash': catalog['publication_hash'], 'claims': [claim]}
    supported = answer.gate_answer(payload, catalog)
    require(supported['claim_receipts'][0]['support_status'] == 'SUPPORTED', 'supported time rejected')
    require(supported['publication_hash'] == catalog['publication_hash'], 'evidence generation drift')
    causal = copy.deepcopy(payload)
    causal['claims'][0]['required_facts'].append({'field': 'cause', 'value': '豪雨'})
    unsupported_cause = answer.gate_answer(causal, catalog)
    require(unsupported_cause['claim_receipts'][0]['support_status'] == 'PARTIAL', 'unsupported cause promoted')
    require(unsupported_cause['safe_claim_ids'] == [], 'partial composite treated as safe')
    conflicting_catalog = evidence_catalog(changed)
    conflicting_payload = copy.deepcopy(payload)
    conflicting_payload['publication_hash'] = conflicting_catalog['publication_hash']
    conflicting_payload['claims'][0]['evidence_ids'] = [d['document_version_id'] for d in changed]
    conflicting = answer.gate_answer(conflicting_payload, conflicting_catalog)
    require(conflicting['claim_receipts'][0]['support_status'] == 'CONFLICT', 'claim conflict erased')
    stale_catalog = copy.deepcopy(catalog)
    for evidence in stale_catalog['evidence']:
        evidence['freshness'] = 'STALE'
    stale = answer.gate_answer(payload, stale_catalog)
    require(stale['claim_receipts'][0]['support_status'] == 'STALE', 'stale evidence treated as current')
    expect_error(lambda: answer.gate_answer(payload, conflicting_catalog), 'cross-generation evidence accepted')
    injected = {**payload, 'evidence': catalog['evidence']}
    expect_error(lambda: answer.gate_answer(injected, catalog), 'caller supplied its own authority')

    # Health over the actual query snapshot cannot pretend an unobserved deployment succeeded.
    source_status, _ = query.load_json(query.DEFAULT_STATUS)
    brief, _ = query.load_json(query.DEFAULT_BRIEF)
    stages = health.current_publication_stages(source_status, brief)
    stages = [s for s in stages if not (s['lane'] == 'query' and s['stage'] == 'query_index')]
    stages.append({'lane': 'query', 'stage': 'query_index', 'outcome': 'SUCCESS', 'generation_id': store['generation_id']})
    health_result = health.build_health(stages)
    require(health_result['lanes']['publication'] != 'HEALTHY', 'missing deployment became healthy')
    failed_stages = copy.deepcopy(stages)
    next(s for s in failed_stages if s['stage'] == 'query_index')['outcome'] = 'FAILED'
    health_failure = health.build_health(failed_stages)
    require(health_failure['lanes']['publication'] == health_result['lanes']['publication'], 'query failure rewrote publication')

    expectations = [
        ('three-source', 'event_pair', {'same_event': True}),
        ('different-date', 'event_pair', {'same_event': False}),
        ('supported-time', 'claim_support', {'support_status': 'SUPPORTED'}),
        ('unsupported-cause', 'claim_support', {'support_status': 'PARTIAL'}),
        ('official-conflict', 'claim_support', {'support_status': 'CONFLICT'}),
        ('stale-current', 'claim_support', {'support_status': 'STALE'}),
    ]
    cases = [{'case_id': cid, 'task': task, 'synthetic': True, 'expected': expected} for cid, task, expected in expectations]
    evaluator.validate_cases(cases)
    predictions = {
        'three-source': {'same_event': len(baseline) == 1},
        'different-date': {'same_event': len(split) == 1},
        'supported-time': {'support_status': supported['claim_receipts'][0]['support_status']},
        'unsupported-cause': {'support_status': unsupported_cause['claim_receipts'][0]['support_status']},
        'official-conflict': {'support_status': conflicting['claim_receipts'][0]['support_status']},
        'stale-current': {'support_status': stale['claim_receipts'][0]['support_status']},
    }
    metrics = evaluator.evaluate({'dataset_id': 'backbone-contract-replay-v1', 'dataset_type': 'SYNTHETIC_CONTRACT_REPLAY'}, cases, predictions)
    require(metrics['missing_prediction_count'] == 0 and metrics['exact_case_match_count'] == len(cases), 'integration evaluator mismatch')
    trace = {'validation_scope': 'SYNTHETIC_INTEGRATION_NOT_PRODUCTION_ACCURACY', 'registry': entity.registry_receipt(registry),
             'documents': documents, 'changed_documents': changed, 'reconciled_documents': reconciled_docs,
             'baseline': baseline, 'conflict': conflict, 'reconciled': reconciled,
             'supported_answer': supported, 'unsupported_cause': unsupported_cause, 'conflicting_answer': conflicting,
             'stale_answer': stale, 'metrics': metrics, 'cases': cases, 'predictions': predictions}
    write_json(output / 'synthetic-integration.json', trace)
    write_json(output / 'health.json', {'scope': 'CHECKED_IN_SNAPSHOT_NOT_LIVE_HEALTH', 'current': health_result, 'injected_query_failure': health_failure})
    return {'status': 'PASS', 'synthetic_evaluation_case_count': len(cases),
            'real_snapshot_item_count': len(store['items']), 'query_generation': store['generation_id'],
            'public_event_id': baseline[0]['public_event_id'], 'production_verified': False,
            'limitations': ['No live official verification or production publication in this harness',
                            'Named event occurrence ID is supplied by the synthetic fixture, not inferred by the registry',
                            'Answer gate checks typed facts; it does not verify arbitrary free-form prose',
                            'Real metadata query and synthetic event trace are distinct datasets',
                            'No Web Chat/MCP, persistent handoff, human gold labels or live 7-day canary is asserted']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--components', type=Path, default=ROOT / '.runtime-checkouts')
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime-evidence/backbone')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    components, output = args.components.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    report = {'schema_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': sha(manifest), 'validation_scope': manifest['validation_scope'],
              'production_verified': False, 'status': 'RUNNING'}
    write_json(output / 'manifest.json', manifest)
    try:
        if args.prepare:
            prepare(manifest, components)
        else:
            for c in manifest['components']:
                actual = execute(['git', 'rev-parse', 'HEAD'], components / c['name'])
                require(actual.returncode == 0 and actual.stdout.strip() == c['sha'], 'pinned checkout required')
        report['components'] = component_suites(manifest, components, output)
        report['integration'] = integration(components, output)
        report['status'] = 'PASS'
    except Exception as error:
        report['status'] = 'FAIL'
        report['error_type'] = type(error).__name__
        report['error'] = str(error)[:10000]
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(output / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
