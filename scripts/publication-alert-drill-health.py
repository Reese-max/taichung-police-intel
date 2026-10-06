#!/usr/bin/env python3
"""Bind actual isolated upload outcomes without inventing production job results."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location('publication_alert', Path(__file__).with_name('publication-alert.py'))
alert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alert)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--outcome', required=True, choices=['success', 'failure'])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    health = {'kind':'GOVINTEL_RUNTIME_HEALTH_RECEIPT', 'execution_scope':'ISOLATED_ARTIFACT_UPLOAD_DRILL',
              'run_id':os.environ.get('GITHUB_RUN_ID'), 'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
              'observed_at':datetime.now(timezone.utc).isoformat(),
              'phase_results':{key:'skipped' for key in alert.PHASES},
              'job_results':{'BUILD_RESULT':'skipped', 'DEPLOY_RESULT':'skipped'},
              'public_data_verified':False, 'query_deployment_verified':False, 'production_verified':False}
    health['phase_results']['PAGES_UPLOAD'] = args.outcome
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(health, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
