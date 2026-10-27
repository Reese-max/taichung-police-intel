#!/usr/bin/env python3
"""Run the change-summary diagnostic on synthetic labelled pairs.

Without --responses it uses the provider configured in the environment, which
is DISABLED by default and then reports evaluation_status=NOT_RUN. --responses
replays scripted output (project-authored, not a model result) (a JSON object of case_id -> response text) with
no network. Only synthetic cases are ever sent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.change_summary import DisabledProvider, ScriptedProvider, provider_from_env
from intel_v2.change_summary_eval import evaluate

DEFAULT_CASES = ROOT / "eval/change-summary/v1/cases.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--responses", type=Path, help="scripted responses for offline replay")
    parser.add_argument("--output", type=Path, help="new file for the report; existing files are never overwritten")
    args = parser.parse_args()

    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.responses:
        responses = json.loads(args.responses.read_text(encoding="utf-8"))
        provider_for = lambda case: ScriptedProvider(responses.get(case["case_id"], '{"statements":[]}'), model="scripted-replay")
    else:
        live = provider_from_env()
        provider_for = lambda case: live
    report = evaluate(cases, provider_for)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise SystemExit("refusing to overwrite an earlier attempt; use a new output path")
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if report["evaluation_status"] == "EVALUATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
