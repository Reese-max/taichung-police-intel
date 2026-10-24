#!/usr/bin/env python3
"""Draft an evidence-bound change summary for one official item version pair.

Default mode replays a synthetic fixture with a scripted offline provider and
transmits nothing. ``--live`` uses the provider named by GOVINTEL_SUMMARY_PROVIDER
and still refuses content whose rights status is not PUBLIC_DERIVED unless the
fixture is marked synthetic. The output is a candidate; it is never published.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.change_summary import ScriptedProvider, SummaryInput, provider_from_env, review_candidate, summarize_change

DEFAULT_FIXTURE = ROOT / "tests/fixtures/change-summary/synthetic-road-notice.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--live", action="store_true", help="use the provider configured in the environment")
    parser.add_argument("--output", type=Path, help="write the summary JSON here instead of stdout")
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    data = SummaryInput(
        identity=fixture["identity"],
        source_id=fixture["source_id"],
        official_url=fixture["official_url"],
        before_text=fixture.get("before_text"),
        after_text=fixture.get("after_text"),
        before_version=fixture.get("before_version"),
        after_version=fixture.get("after_version"),
        rights_status=fixture.get("rights_status", "UNKNOWN"),
        synthetic=fixture.get("synthetic") is True,
    )
    if args.live:
        provider = provider_from_env()
    else:
        if "good_response" not in fixture:
            raise SystemExit("offline replay needs a fixture with good_response")
        provider = ScriptedProvider(json.dumps(fixture["good_response"], ensure_ascii=False))
    summary = summarize_change(data, provider)
    result = {"summary": summary, "review_candidate": review_candidate(summary)}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if summary["status"] == "PENDING_REVIEW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
