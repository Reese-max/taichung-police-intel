#!/usr/bin/env python3
"""Audit actual S036/CTX165 original CSV bytes locally; never promote or emit rows."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re

MAX_BYTES = 64 * 1024 * 1024
HEADERS = {
    'S-036': ['actStTime', 'actEndTime', 'actCategory', 'placeOrRoute', 'authorities'],
    'CTX-165': ['民國年月', '網域', '網站性質', '法律依據', '聲請單位'],
}
ASSEMBLY_LABELS = ['活動開始時間', '活動結束時間', '活動類別', '集會處所或遊行路線', '主管機關']


def audit(body, *, source_id, expected_sha256):
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
    periods = Counter()
    earliest = latest = None
    invalid_times = end_before = empty = invalid_periods = count = 0
    for row in reader:
        if len(row) != len(HEADERS[source_id]):
            raise ValueError('original CSV row width changed')
        if source_id == 'S-036' and row == ASSEMBLY_LABELS:
            raise ValueError('S-036 description row duplicated')
        count += 1
        # Hash identities in memory rather than retaining incident/domain text.
        seen[hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()] += 1
        empty += sum(not value.strip() for value in row)
        if source_id == 'S-036':
            try:
                if any(not re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}', value) for value in row[:2]):
                    raise ValueError('time shape')
                start, end = (datetime.strptime(value, '%Y/%m/%d %H:%M') for value in row[:2])
                earliest = start if earliest is None else min(earliest, start)
                latest = start if latest is None else max(latest, start)
                end_before += end < start
            except ValueError:
                invalid_times += 1
        else:
            if not re.fullmatch(r'\d{3}(?:0[1-9]|1[0-2])', row[0]) or int(row[0][:3]) == 0:
                invalid_periods += 1
            else:
                periods[row[0]] += 1
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
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id', choices=sorted(HEADERS), required=True)
    parser.add_argument('--resource', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    with args.resource.open('rb') as handle:
        body = handle.read(MAX_BYTES + 1)
    result = audit(body, source_id=args.source_id, expected_sha256=args.expected_sha256)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        args.out.write_text(encoded, encoding='utf-8')
    else:
        print(encoded, end='')
    return 0 if result['quality_status'] == 'CONTRACT_CHECKS_PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
