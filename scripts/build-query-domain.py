#!/usr/bin/env python3
"""Build validated, rebuildable PublicEvent/statistics query projections."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2 import query_domain


def build_stores(
    *, public_events: Path | None = None, statistics: Path | None = None
) -> dict[str, dict[str, Any]]:
    stores: dict[str, dict[str, Any]] = {}
    if public_events is not None:
        stores["public_events"] = query_domain.load_event_store(public_events)
    if statistics is not None:
        stores["statistics"] = query_domain.load_statistics_store(statistics)
    if not stores:
        raise ValueError("at least one canonical domain input is required")
    return stores


def write_store(path: Path, store: dict[str, Any]) -> None:
    if store.get("store_type") == "PUBLIC_EVENT_QUERY":
        query_domain.validate_event_store(store)
    elif store.get("store_type") == "TYPED_STATISTICS_QUERY":
        query_domain.validate_statistics_store(store)
    else:
        raise ValueError("unsupported domain store type")
    payload = query_domain.canonical(store) + b"\n"
    if len(payload) > query_domain.MAX_BYTES:
        raise ValueError("domain store output exceeds byte limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--public-events", type=Path)
    build.add_argument("--public-events-output", type=Path)
    build.add_argument("--statistics", type=Path)
    build.add_argument("--statistics-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pairs = (
        (args.public_events, args.public_events_output, "public-events"),
        (args.statistics, args.statistics_output, "statistics"),
    )
    for source, destination, label in pairs:
        if (source is None) != (destination is None):
            raise SystemExit(f"--{label} and --{label}-output must be supplied together")
        if source is not None and destination is not None and source.resolve() == destination.resolve():
            raise SystemExit(f"--{label} and --{label}-output must be different paths")
    if args.public_events_output and args.statistics_output and args.public_events_output.resolve() == args.statistics_output.resolve():
        raise SystemExit("domain store outputs must be different paths")
    stores = build_stores(public_events=args.public_events, statistics=args.statistics)
    if args.public_events_output:
        write_store(args.public_events_output, stores["public_events"])
    if args.statistics_output:
        write_store(args.statistics_output, stores["statistics"])
    summary = [
        f"{kind}_generation={store['generation_id']}"
        for kind, store in stores.items()
    ]
    print("QUERY_DOMAIN_BUILD_OK " + " ".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
