#!/usr/bin/env python3
"""Local-only S-038 canary. No network unless --live is explicitly supplied."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_v2.npa_news import MAX_BYTES, collect_snapshot, fetch_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--live", action="store_true")
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--state-output", type=Path)
    args = parser.parse_args()
    if args.live:
        body, transport = fetch_csv()
    else:
        with args.input.open("rb") as handle:
            body = handle.read(MAX_BYTES + 1)
        transport = {"mode": "OFFLINE"}
    previous = json.loads(args.previous.read_text()) if args.previous else None
    receipt, state = collect_snapshot(body, observed_at=datetime.now(timezone.utc).isoformat(),
                                      previous=previous, transport=transport)
    if args.state_output:
        # Explicit local output only. Atomic replacement after complete validation.
        temporary = args.state_output.with_suffix(args.state_output.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.state_output)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Transport exceptions may contain untrusted remote text; do not print it.
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "state_advanced": False, "display_empty": False}), file=sys.stderr)
        sys.exit(1)
