#!/usr/bin/env python3
"""Offline S028/CTXPOP declaration, original-byte and hash-only quality audit."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

MAX_BYTES = 4 * 1024 * 1024
MAX_RESOURCES = 128
FIELDS = frozenset(('地區', '項目', '欄位名稱', '數值', '資料時間日期', '資料週期',
                    '郵遞區號', '機關代碼', '電子郵件', '行動電話', '市話', '縣市別代碼', '行政區域代碼'))
SOURCE_CYCLES = {'S-028': '月', 'CTX-POP': '年'}
CANDIDATE_KEY_FIELDS = ('資料時間日期', '地區', '項目', '欄位名稱')
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
SHA256 = re.compile(r'[0-9a-f]{64}')
DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}')
_spec = importlib.util.spec_from_file_location('inventory_identity', Path(__file__).with_name('npa-source-inventory.py'))
_inventory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inventory)
_csv_spec = importlib.util.spec_from_file_location('csv_audit_io', Path(__file__).with_name('reference-csv-audit.py'))
_csv_audit = importlib.util.module_from_spec(_csv_spec)
_csv_spec.loader.exec_module(_csv_audit)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


def decode(body):
    def invalid_constant(_):
        raise ValueError('nonfinite JSON number')
    return json.loads(body.decode('utf-8-sig'), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def positive_int(value):
    return type(value) is int and value > 0


def candidate_grain_profile(period_candidates):
    """Aggregate exact schema fields without exposing labels or assigning official grain."""
    profiles = []
    item_sets, column_sets = set(), set()
    first_items = first_columns = None
    conflicts = duplicates = 0
    for period, candidates in sorted(period_candidates.items()):
        regions, items, columns = set(), set(), set()
        columns_by_item = defaultdict(set)
        for key in candidates:
            _exact_date, region, item, column = key
            regions.add(region)
            items.add(item)
            columns.add(column)
            columns_by_item[item].add(column)
        items, columns = frozenset(items), frozenset(columns)
        item_sets.add(items)
        column_sets.add(columns)
        if first_items is None:
            first_items, first_columns = items, columns
        multiplicities = [sum(values.values()) for values in candidates.values()]
        local_conflicts = sum(len(values) > 1 for values in candidates.values())
        local_duplicates = sum(count - 1 for count in multiplicities)
        conflicts += local_conflicts
        duplicates += local_duplicates
        profiles.append({
            'period': period, 'candidate_rows': sum(multiplicities),
            'distinct_regions': len(regions), 'distinct_items': len(items),
            'distinct_columns': len(columns),
            'distinct_item_column_pairs': sum(len(values) for values in columns_by_item.values()),
            'candidate_key_groups': len(candidates),
            'duplicate_candidate_key_rows': local_duplicates,
            'conflicting_value_candidate_key_groups': local_conflicts,
            'candidate_key_multiplicity_histogram': dict(sorted(Counter(multiplicities).items())),
            'item_column_count_histogram': dict(sorted(Counter(len(values) for values in columns_by_item.values()).items())),
            'all_items_share_same_column_set': len({frozenset(values) for values in columns_by_item.values()}) == 1,
            'item_set_equals_first_observed_period': items == first_items,
            'column_set_equals_first_observed_period': columns == first_columns,
        })
    return {
        'scope': 'CANDIDATE_UNREVIEWED_ROWS_IN_VERIFIED_RESOURCES_ONLY',
        'candidate_key_fields': list(CANDIDATE_KEY_FIELDS),
        'candidate_key_semantics': 'EXACT_DECLARED_SCHEMA_FIELDS_ONLY_NOT_OFFICIAL_STATISTICAL_GRAIN',
        'candidate_key_comparison': 'EXACT_FULL_DATE_AND_LABEL_STRINGS_PERIOD_GROUPING_DOES_NOT_REPLACE_KEY_FIELDS',
        'value_comparison': 'EXACT_DECODED_STRING_ONLY_NO_NUMERIC_COERCION',
        'raw_dimension_labels_included': False, 'row_hashes_or_positions_included': False,
        'distinct_observed_item_sets': len(item_sets), 'distinct_observed_column_sets': len(column_sets),
        'duplicate_candidate_key_rows': duplicates, 'conflicting_value_candidate_key_groups': conflicts,
        'structural_variation_observed': len(item_sets) > 1 or len(column_sets) > 1,
        'periods': profiles,
    }


def audit_collection(*, source_id, declaration, receipts, resource_root,
                     include_disposition_index=False):
    if source_id not in SOURCE_CYCLES:
        raise ValueError('unsupported JSON collection contract')
    if (not isinstance(declaration, dict) or declaration.get('inventory_id') != source_id
            or type(declaration.get('http_status')) is not int or declaration['http_status'] != 200
            or not isinstance(declaration.get('raw_sha256'), str)
            or not SHA256.fullmatch(declaration['raw_sha256'])):
        raise ValueError('source-bound successful metadata receipt required')
    resources = declaration.get('resources')
    if (not isinstance(resources, list) or not 1 <= len(resources) <= MAX_RESOURCES
            or not positive_int(declaration.get('resource_count'))
            or declaration['resource_count'] != len(resources)):
        raise ValueError('nonempty bounded consistent declaration required')
    declared = {}
    for resource in resources:
        if not isinstance(resource, dict) or not isinstance(resource.get('resource_url'), str):
            raise ValueError('invalid declared resource URL')
        url = urlsplit(resource['resource_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('declared resource URL must be credential-free HTTPS')
        # Reuse the repaired inventory identity parser; old receipts can contain
        # null IDs while their unchanged original URL unambiguously declares rid.
        derived_id = _inventory._resource_id(resource['resource_url'])
        rid = resource.get('resource_id') or derived_id
        if (not isinstance(rid, str) or not UUID.fullmatch(rid) or rid != derived_id or rid in declared
                or resource.get('format') != 'JSON' or type(resource.get('field_count')) is not int
                or resource['field_count'] != len(FIELDS)
                or resource.get('resource_id') not in (None, rid)):
            raise ValueError('invalid/duplicate declared JSON resource contract')
        declared[rid] = resource
    if not isinstance(receipts, list) or len(receipts) > MAX_RESOURCES * 8:
        raise ValueError('bounded download receipt array required')
    selected = {}
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError('invalid download receipt')
        if receipt.get('source_id') != source_id:
            continue
        rid = receipt.get('resource_id')
        if rid not in declared or rid in selected:
            raise ValueError('unexpected/duplicate source download receipt')
        selected[rid] = receipt

    root = Path(resource_root).resolve()
    results, positions, disposition_rows = [], defaultdict(list), []
    periods, quarantine_reasons, file_hashes = Counter(), Counter(), defaultdict(list)
    candidate_hashes, count = Counter(), 0
    period_candidates = defaultdict(lambda: defaultdict(Counter))
    acquired = quarantined = 0
    for rid, resource in sorted(declared.items()):
        item = {'resource_id': rid, 'status': 'REJECTED', 'resource_rows': None}
        receipt = selected.get(rid)
        if receipt is None:
            item['reason_code'] = 'DOWNLOAD_RECEIPT_ABSENT'
            results.append(item)
            continue
        if (type(receipt.get('http_status')) is not int or receipt['http_status'] != 200
                or receipt.get('requested_url') != resource['resource_url']
                or receipt.get('final_url') != resource['resource_url']
                or not positive_int(receipt.get('bytes')) or receipt['bytes'] > MAX_BYTES
                or not isinstance(receipt.get('sha256'), str) or not SHA256.fullmatch(receipt['sha256'])):
            item['reason_code'] = 'DOWNLOAD_RECEIPT_CONTRACT_MISMATCH'
            results.append(item)
            continue
        path = root / (source_id + '-' + rid + '-original.json')
        # Symlinks must remain inside the selected private raw directory.
        if not path.resolve().is_relative_to(root):
            raise ValueError('original resource path escapes resource root')
        try:
            body = _csv_audit.read_regular_bounded(path, MAX_BYTES)
        except (OSError, ValueError):
            item['reason_code'] = 'ORIGINAL_BYTES_ABSENT_OR_UNREADABLE'
            results.append(item)
            continue
        actual_hash = hashlib.sha256(body).hexdigest()
        item.update(resource_bytes=len(body), resource_sha256=actual_hash)
        if len(body) != receipt['bytes'] or actual_hash != receipt['sha256']:
            item['reason_code'] = 'ORIGINAL_BYTES_OR_HASH_MISMATCH'
            results.append(item)
            continue
        try:
            rows = decode(body)
            if (not isinstance(rows, list) or not rows
                    or any(not isinstance(row, dict) or set(row) != FIELDS
                           or any(not isinstance(value, str) for value in row.values()) for row in rows)):
                raise ValueError('nonempty thirteen-string-field array required')
        except (UnicodeError, ValueError):
            item['reason_code'] = 'JSON_DECODE_OR_SCHEMA_REJECTED'
            results.append(item)
            continue
        acquired += 1
        file_hashes[actual_hash].append(rid)
        local_counts, local_periods, local_quarantine = Counter(), Counter(), 0
        for ordinal, row in enumerate(rows, 1):
            count += 1
            row_hash = digest(row)
            local_counts[row_hash] += 1
            positions[row_hash].append({'resource_id': rid, 'json_row_number': ordinal})
            reasons = []
            if not row['數值'].strip():
                reasons.append('EMPTY_VALUE')
            if any(not row[key].strip() for key in ('地區', '項目', '欄位名稱')):
                reasons.append('EMPTY_IDENTITY_CANDIDATE_FIELD')
            if row['資料週期'] != SOURCE_CYCLES[source_id]:
                reasons.append('UNEXPECTED_DECLARED_CYCLE')
            try:
                date_string = row['資料時間日期']
                if not DATE.fullmatch(date_string):
                    raise ValueError('date shape')
                date = datetime.strptime(date_string, '%Y-%m-%dT%H:%M:%S')
                if (date.day != 1 or (date.hour, date.minute, date.second) != (0, 0, 0)
                        or (source_id == 'CTX-POP' and date.month != 1)):
                    raise ValueError('period boundary')
                period = f'{date.year:04d}-{date.month:02d}' if source_id == 'S-028' else f'{date.year:04d}'
                periods[period] += 1
                local_periods[period] += 1
            except ValueError:
                reasons.append('INVALID_COLLECTION_PERIOD')
            if reasons:
                quarantined += 1
                local_quarantine += 1
                quarantine_reasons.update(reasons)
            else:
                candidate_hashes[row_hash] += 1
                key = tuple(row[field] for field in CANDIDATE_KEY_FIELDS)
                period_candidates[period][key][row['數值']] += 1
            if include_disposition_index:
                disposition_rows.append({'resource_id': rid, 'resource_sha256': actual_hash,
                                         'json_row_number': ordinal, 'row_sha256': row_hash,
                                         'disposition': 'QUARANTINED' if reasons else 'CANDIDATE_UNREVIEWED',
                                         'reason_codes': reasons})
        item.update(status='BYTES_AND_SCHEMA_VERIFIED', resource_rows=len(rows),
                    period_rows=dict(sorted(local_periods.items())), quarantined_rows=local_quarantine,
                    duplicate_full_rows=sum(n - 1 for n in local_counts.values()))
        results.append(item)

    missing_periods = []
    if periods:
        if source_id == 'S-028':
            first, last = sorted(periods)[0], sorted(periods)[-1]
            year, month = map(int, first.split('-'))
            while f'{year:04d}-{month:02d}' <= last:
                period = f'{year:04d}-{month:02d}'
                if period not in periods:
                    missing_periods.append(period)
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        else:
            missing_periods = [f'{year:04d}' for year in range(int(min(periods)), int(max(periods)) + 1)
                               if f'{year:04d}' not in periods]
    duplicate_groups = {key: refs for key, refs in positions.items() if len(refs) > 1}
    cross_groups = {key: refs for key, refs in duplicate_groups.items()
                    if len({ref['resource_id'] for ref in refs}) > 1}
    cross_duplicates = sum(len(refs) - max(Counter(ref['resource_id'] for ref in refs).values())
                           for refs in cross_groups.values())
    duplicates = sum(len(refs) - 1 for refs in duplicate_groups.values())
    grain_profile = candidate_grain_profile(period_candidates)
    result = {
        'schema_version': 1, 'source_id': source_id,
        'validation_scope': 'LOCAL_CURRENT_METADATA_RESOURCE_SET_ONLY',
        'metadata_sha256': declaration['raw_sha256'], 'declaration_receipt_sha256': digest(declaration),
        'resource_identity_method': 'EXISTING_INVENTORY_PARSER_UNAMBIGUOUS_ORIGINAL_URL_AND_DECLARED_ID_MATCH',
        'download_receipts_sha256': digest([selected[rid] for rid in sorted(selected)]),
        'declared_resources': len(declared), 'verified_resources': acquired,
        'declaration_coverage': 'ALL_DECLARED_BYTES_AND_SCHEMA_VERIFIED' if acquired == len(declared) else 'PARTIAL',
        'resources': results, 'data_rows': count,
        'candidate_unreviewed_rows': count - quarantined, 'candidate_unique_full_rows': len(candidate_hashes),
        'quarantined_rows': quarantined, 'quarantine_reason_rows': dict(sorted(quarantine_reasons.items())),
        'duplicate_full_rows': duplicates, 'duplicate_groups': len(duplicate_groups),
        'cross_resource_exact_duplicate_rows': cross_duplicates,
        'cross_resource_duplicate_groups': len(cross_groups),
        'cross_resource_duplicate_definition': 'TOTAL_GROUP_MULTIPLICITY_MINUS_LARGEST_SINGLE_RESOURCE_MULTIPLICITY',
        'identical_resource_groups': [{'resource_sha256': key, 'resource_ids': sorted(ids)}
                                      for key, ids in sorted(file_hashes.items()) if len(ids) > 1],
        'observed_period_rows': dict(sorted(periods.items())), 'missing_observed_span_periods': missing_periods,
        'gap_scope': 'OBSERVED_COLLECTION_SPAN_ONLY_NOT_WORLD_ZERO_OR_EXPECTED_BUSINESS_COVERAGE',
        'source_timezone': 'UNKNOWN', 'period_semantics': 'NOT_VERIFIED',
        'candidate_grain_profile': grain_profile,
        'aggregation_guard': {
            'status': 'NOT_ALLOWED', 'within_period_aggregation_allowed': False,
            'cross_period_aggregation_allowed': False, 'deduplication_allowed': False,
            'reason_codes': ['OFFICIAL_STATISTICAL_GRAIN_NOT_VERIFIED',
                             'MEASURE_ADDITIVITY_NOT_VERIFIED', 'RIGHTS_REVIEW_PENDING'],
        },
        'business_scope_completeness': 'NOT_VERIFIED', 'business_identity': 'UNKNOWN',
        'rights_review': 'PENDING', 'production_active': False, 'multiplicity_retained': True,
        'original_bytes_modified': False, 'model_transmission_allowed': False,
        'row_hash_index_anonymization_approved': False,
        'quality_status': 'QUALITY_ISSUES' if (duplicates or quarantined or missing_periods
                                             or grain_profile['conflicting_value_candidate_key_groups']) else 'CONTRACT_CHECKS_PASS',
    }
    if acquired != len(declared):
        result['quality_status'] = 'RESOURCE_CONTRACT_BLOCKED'
    if include_disposition_index:
        result['private_disposition_index'] = {
            'schema_version': 1, 'source_id': source_id, 'raw_fields_included': False,
            'declaration_receipt_sha256': result['declaration_receipt_sha256'],
            'row_hash_algorithm': 'SHA256(UTF8(json.dumps(decoded_json_row, ensure_ascii=False, sort_keys=True, allow_nan=False)))',
            'row_number_semantics': 'ONE_BASED_JSON_ARRAY_POSITION', 'rows': disposition_rows,
            'duplicate_groups': [{'row_sha256': key, 'multiplicity': len(refs), 'references': refs}
                                 for key, refs in sorted(duplicate_groups.items())],
            'multiplicity_retained': True, 'business_identity': 'UNKNOWN',
            'rights_review': 'PENDING', 'production_active': False,
            'model_transmission_allowed': False, 'row_hash_index_anonymization_approved': False,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id', choices=sorted(SOURCE_CYCLES), required=True)
    parser.add_argument('--metadata-receipts', type=Path, required=True)
    parser.add_argument('--download-receipts', type=Path, required=True)
    parser.add_argument('--resource-root', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--private-index-out', type=Path)
    args = parser.parse_args()
    outputs = [path.resolve() for path in (args.out, args.private_index_out) if path is not None]
    protected = {args.metadata_receipts.resolve(), args.download_receipts.resolve()}
    if (len(outputs) != len(set(outputs)) or any(path in protected for path in outputs)
            or any(path.is_relative_to(args.resource_root.resolve()) for path in outputs)):
        parser.error('outputs must be distinct and outside original resource directory')
    def load_bounded(path):
        body = _csv_audit.read_regular_bounded(path, MAX_BYTES)
        if not body or len(body) > MAX_BYTES:
            raise ValueError('receipt empty or exceeds4MiB')
        return decode(body)
    metadata = load_bounded(args.metadata_receipts)
    if not isinstance(metadata, list):
        raise ValueError('metadata receipt array required')
    selected = [item for item in metadata if isinstance(item, dict) and item.get('inventory_id') == args.source_id]
    if len(selected) != 1:
        raise ValueError('exactly one source metadata receipt required')
    result = audit_collection(source_id=args.source_id, declaration=selected[0],
                              receipts=load_bounded(args.download_receipts), resource_root=args.resource_root,
                              include_disposition_index=args.private_index_out is not None)
    private_index = result.pop('private_disposition_index', None)
    if args.private_index_out:
        _csv_audit.write_json(args.private_index_out, private_index)
    if args.out:
        _csv_audit.write_json(args.out, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['quality_status'] == 'CONTRACT_CHECKS_PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
