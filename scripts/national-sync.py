#!/usr/bin/env python3
"""Run fixture-only national metadata ingestion; no production adapters in W1."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from national.adapters.fixture import FixtureAdapter
from national.loader import check_guards, load
from national.store import SCHEMA, SqliteStore, WranglerD1Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source")
    parser.add_argument("--adapter", choices=["fixture"])
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--store", default="d1-local")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply-schema", action="store_true")
    parser.add_argument("--check-guards", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.adapter and (not args.fixture or not args.source):
        parser.error("--adapter fixture requires --fixture and --source")
    if args.fixture and not args.adapter:
        parser.error("--fixture requires --adapter fixture")
    if args.dry_run and args.check_guards:
        parser.error("--dry-run and --check-guards are mutually exclusive")
    if args.store not in {"d1-local", "d1-remote"} and not args.store.startswith("sqlite:"):
        parser.error("--store must be sqlite:<path>, d1-local or d1-remote")
    registry = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))
    sources = registry["sources"]
    if args.source:
        sources = [source for source in sources if source["national_source_id"] == args.source]
        if not sources:
            parser.error("unknown source")
    runner = os.environ.get("NATIONAL_RUNNER", "github-actions")
    report = {"runner": runner, "runs": [], "exit_code": 0}
    store = None
    try:
        if not args.dry_run:
            store = (SqliteStore(args.store[7:]) if args.store.startswith("sqlite:") else
                     WranglerD1Store(remote=args.store == "d1-remote"))
        if args.apply_schema:
            if args.dry_run:
                print(SCHEMA.read_text(encoding="utf-8"))
            else:
                report["schema_rows_written"] = store.apply_schema()
        if args.check_guards:
            report.update(check_guards(store, capacity_limit_mb=registry["capacity_limit_mb"]))
            report["exit_code"] = int(report["status"] == "CAPACITY_BLOCKED")
        elif args.adapter:
            result = load(store, sources[0], FixtureAdapter(args.fixture), runner=runner,
                          capacity_limit_mb=registry["capacity_limit_mb"], dry_run=args.dry_run)
            report["runs"].append(result)
            report["exit_code"] = result["exit_code"]
        elif not args.apply_schema:
            for source in sources:
                report["runs"].append({"source_id": source["national_source_id"], "status": "NOT_CONFIGURED",
                                       "reason": "RUNNER_MISMATCH" if source["runner"] != runner else "NO_ADAPTER"})
            report["message"] = "無可同步來源：W1 未配置網路 adapter"
    except Exception as error:
        report.update(status="ERROR", error=type(error).__name__ + ": " + str(error), exit_code=1)
    finally:
        if store is not None:
            store.close()
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output, file=sys.stderr if args.dry_run else sys.stdout)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
