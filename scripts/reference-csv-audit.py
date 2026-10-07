#!/usr/bin/env python3
"""Audit actual S036/CTX165 original CSV bytes locally; never promote or emit rows."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MAX_BYTES = 64 * 1024 * 1024
HEADERS = {
    'S-036': ['actStTime', 'actEndTime', 'actCategory', 'placeOrRoute', 'authorities'],
    'CTX-165': ['民國年月', '網域', '網站性質', '法律依據', '聲請單位'],
}
ASSEMBLY_LABELS = ['活動開始時間', '活動結束時間', '活動類別', '集會處所或遊行路線', '主管機關']


def row_sha256(row):
    """Exact decoded-field identity; never a business/event/domain identity."""
    return hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()


def audit(body, *, source_id, expected_sha256, include_disposition_index=False):
    if source_id not in HEADERS:
        raise ValueError('unsupported CSV source contract')
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256 or ''):
        raise ValueError('explicit lowercase SHA256 required')
    if not body or len(body) > MAX_BYTES:
        raise ValueError('original CSV empty or exceeds64MiB')
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected_sha256:
        raise ValueError('original resource SHA256 mismatch')
    if body.lstrip().startswith(b'<'):
        raise ValueError('HTML/error response is not CSV')
    reader = csv.reader(io.StringIO(body.decode('utf-8-sig')), strict=True)
    if next(reader, None) != HEADERS[source_id]:
        raise ValueError('original CSV schema changed')
    description = 0
    if source_id == 'S-036':
        if next(reader, None) != ASSEMBLY_LABELS:
            raise ValueError('S-036 description row missing or changed')
        description = 1
    seen = Counter()
    candidate_seen = Counter()
    reasons = Counter()
    dispositions = []
    group_positions = {}
    periods = Counter()
    earliest = latest = None
    invalid_times = end_before = empty = invalid_periods = count = quarantined = 0
    for position, row in enumerate(reader):
        if len(row) != len(HEADERS[source_id]):
            raise ValueError('original CSV row width changed')
        if source_id == 'S-036' and row == ASSEMBLY_LABELS:
            raise ValueError('S-036 description row duplicated')
        count += 1
        # Hash identities in memory rather than retaining incident/domain text.
        identity = row_sha256(row)
        seen[identity] += 1
        row_reasons = []
        empty_count = sum(not value.strip() for value in row)
        empty += empty_count
        if empty_count:
            row_reasons.append('EMPTY_REQUIRED_VALUE')
        if source_id == 'S-036':
            try:
                if any(not re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}', value) for value in row[:2]):
                    raise ValueError('time shape')
                start, end = (datetime.strptime(value, '%Y/%m/%d %H:%M') for value in row[:2])
                earliest = start if earliest is None else min(earliest, start)
                latest = start if latest is None else max(latest, start)
                end_before += end < start
                if end < start:
                    row_reasons.append('END_BEFORE_START')
            except ValueError:
                invalid_times += 1
                row_reasons.append('INVALID_LOCAL_TIME')
        else:
            if not re.fullmatch(r'\d{3}(?:0[1-9]|1[0-2])', row[0]) or int(row[0][:3]) == 0:
                invalid_periods += 1
                row_reasons.append('INVALID_ROC_PERIOD')
            else:
                periods[row[0]] += 1
        if row_reasons:
            quarantined += 1
            reasons.update(row_reasons)
        else:
            candidate_seen[identity] += 1
        if include_disposition_index:
            # Record ordinal, not physical line number: quoted cells can span lines.
            record_number = position + 2 + description
            group_positions.setdefault(identity, []).append(record_number)
            dispositions.append({'csv_record_number': record_number, 'row_sha256': identity,
                                 'disposition': 'QUARANTINED' if row_reasons else 'CANDIDATE_UNREVIEWED',
                                 'reason_codes': row_reasons})
    if count == 0:
        raise ValueError('no actual data rows')
    duplicates = sum(value - 1 for value in seen.values())
    result = {
        'schema_version': 1, 'source_id': source_id, 'resource_sha256': actual,
        'resource_bytes': len(body), 'data_rows': count, 'description_rows': description,
        'empty_cells': empty, 'duplicate_full_data_rows': duplicates,
        'quality_status': 'QUALITY_ISSUES' if (empty or duplicates or invalid_times or end_before or invalid_periods) else 'CONTRACT_CHECKS_PASS',
        'business_scope_completeness': 'NOT_VERIFIED', 'rights_review': 'PENDING',
        'production_active': False, 'identity_semantics': 'NOT_VERIFIED',
        'row_identity_semantics': 'EXACT_DECODED_FIELDS_ONLY',
        'business_identity': 'UNKNOWN',
        'disposition_scope': 'STRUCTURAL_CONTRACT_ONLY_NOT_PRODUCTION_USABILITY',
        'candidate_unreviewed_rows': count - quarantined,
        'candidate_unique_full_rows': len(candidate_seen),
        'candidate_duplicate_full_rows': sum(n - 1 for n in candidate_seen.values()),
        'quarantined_rows': quarantined,
        'quarantine_reason_rows': dict(sorted(reasons.items())),
        'duplicate_groups': sum(n > 1 for n in seen.values()),
        'multiplicity_retained': True,
        'original_bytes_modified': False, 'model_transmission_allowed': False,
        'row_hash_index_anonymization_approved': False,
    }
    if source_id == 'S-036':
        result.update(invalid_time_rows=invalid_times, end_before_start_count=end_before,
                      first_start=earliest.isoformat() if earliest else None,
                      last_start=latest.isoformat() if latest else None,
                      source_timezone='UNKNOWN', time_comparison_scope='SOURCE_LOCAL_STRINGS_ONLY')
    else:
        result.update(invalid_period_rows=invalid_periods, observed_roc_periods=sorted(periods),
                      period_rows=dict(sorted(periods.items())), local_case_count_allowed=False,
                      domain_identity_validation='NOT_RUN')
    if include_disposition_index:
        result['private_disposition_index'] = {
            'schema_version': 1, 'source_id': source_id,
            'resource_sha256': actual,
            'row_hash_algorithm': 'SHA256(UTF8(json.dumps(decoded_csv_fields, ensure_ascii=False)))',
            'record_number_semantics': 'ONE_BASED_CSV_RECORD_HEADER_INCLUDED',
            'raw_fields_included': False,
            'rows': dispositions,
            'duplicate_groups': [{'row_sha256': identity, 'multiplicity': len(positions),
                                  'csv_record_numbers': positions}
                                 for identity, positions in sorted(group_positions.items()) if len(positions) > 1],
            'production_active': False, 'rights_review': 'PENDING',
            'business_identity': 'UNKNOWN', 'multiplicity_retained': True,
            'model_transmission_allowed': False, 'row_hash_index_anonymization_approved': False,
        }
    return result


def write_json(path, result):
    """Atomic private file; replacing a hardlink never edits its original inode."""
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(encoded)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_regular_bounded(path, limit):
    """Avoid FIFO open blocking before the byte limit can apply."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('local input must be a regular file')
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            return handle.read(limit + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id', choices=sorted(HEADERS), required=True)
    parser.add_argument('--resource', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--private-index-out', type=Path,
                        help='Optional hash/record-ordinal disposition index; keep outside public publications.')
    args = parser.parse_args()
    outputs = [path.resolve() for path in (args.out, args.private_index_out) if path is not None]
    if args.resource.resolve() in outputs or len(outputs) != len(set(outputs)):
        parser.error('outputs must differ from original resource and each other')
    body = read_regular_bounded(args.resource, MAX_BYTES)
    result = audit(body, source_id=args.source_id, expected_sha256=args.expected_sha256,
                   include_disposition_index=args.private_index_out is not None)
    private_index = result.pop('private_disposition_index', None)
    if args.private_index_out:
        write_json(args.private_index_out, private_index)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        write_json(args.out, result)
    else:
        print(encoded, end='')
    return 0 if result['quality_status'] == 'CONTRACT_CHECKS_PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
