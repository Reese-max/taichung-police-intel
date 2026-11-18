#!/usr/bin/env python3
"""Sync registered official national metadata sources or an explicit offline fixture."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from national.adapters.fixture import FixtureAdapter
from national.adapters.kee import KeeAdapter
from national.adapters.ly import Ly20Adapter, Ly6Adapter
from national.adapters.pcc import PccGpaAdapter, PccWebAdapter
from national.adapters.txg import TxgAdapter
from national.adapters.tnn import TnnAdapter
from national.adapters.khh import KhhAdapter
from national.adapters.tpe import TpeAdapter
from national.adapters.tdx import TdxAdapter
from national.adapters.odp import OdpCatalogAdapter
from national.loader import check_guards, load
from national.store import SCHEMA, SqliteStore, WranglerD1Store

ADAPTERS = {"txg": TxgAdapter, "kee": KeeAdapter, "tnn": TnnAdapter, "khh": KhhAdapter, "tpe": TpeAdapter,
            "ly20": Ly20Adapter, "ly6": Ly6Adapter, "pcc_gpa": PccGpaAdapter, "pcc_web": PccWebAdapter,
            "tdx": TdxAdapter, "odp": OdpCatalogAdapter}
SOURCE_ADAPTERS = {**{"N-COUNCIL-" + code.upper(): code for code in ("txg", "kee", "tnn", "khh", "tpe")},
                   "N-LY-ID20-BILLS": "ly20", "N-LY-ID6-INTERP": "ly6",
                   "N-PCC-GPA": "pcc_gpa", "N-PCC-WEB": "pcc_web",
                   "N-TDX-EVENTS": "tdx", "N-ODP-CATALOG": "odp"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source")
    parser.add_argument("--adapter", choices=["fixture", *ADAPTERS])
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--store", default="d1-local")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="Use the full PCC web month window")
    parser.add_argument("--apply-schema", action="store_true")
    parser.add_argument("--check-guards", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.adapter == "fixture" and (not args.fixture or not args.source):
        parser.error("--adapter fixture requires --fixture and --source")
    if args.fixture and args.adapter != "fixture":
        parser.error("--fixture requires --adapter fixture")
    if args.adapter in ADAPTERS and SOURCE_ADAPTERS.get(args.source) != args.adapter:
        parser.error("network adapter requires its matching --source")
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

    def open_store():
        nonlocal store
        if store is None and not args.dry_run:
            store = (SqliteStore(args.store[7:]) if args.store.startswith("sqlite:") else
                     WranglerD1Store(remote=args.store == "d1-remote"))

    try:
        if args.apply_schema:
            open_store()
            if args.dry_run:
                print(SCHEMA.read_text(encoding="utf-8"))
            else:
                report["schema_rows_written"] = store.apply_schema()
        if args.check_guards:
            open_store()
            report.update(check_guards(store, capacity_limit_mb=registry["capacity_limit_mb"]))
            report["exit_code"] = int(report["status"] == "CAPACITY_BLOCKED")
        elif args.adapter == "fixture":
            open_store()
            result = load(store, sources[0], FixtureAdapter(args.fixture), runner=runner,
                          capacity_limit_mb=registry["capacity_limit_mb"], dry_run=args.dry_run)
            report["runs"].append(result)
            report["exit_code"] = result["exit_code"]
        elif args.adapter or not args.apply_schema:
            for source in sources:
                adapter_name = SOURCE_ADAPTERS.get(source["national_source_id"])
                if source["runner"] != runner or adapter_name is None:
                    if args.source:
                        report["runs"].append({"source_id": source["national_source_id"],
                                               "status": "NOT_CONFIGURED", "warnings": [],
                                               "reason": "RUNNER_MISMATCH" if source["runner"] != runner else "NO_ADAPTER"})
                    continue
                if adapter_name == "tdx" and not TdxAdapter.configured():
                    report["runs"].append({"source_id": source["national_source_id"],
                                           "status": "NOT_CONFIGURED", "reason": "MISSING_SECRET",
                                           "warnings": [], "exit_code": 0})
                    continue
                open_store()
                options = {"backfill": args.backfill} if adapter_name == "pcc_web" else {}
                adapter = ADAPTERS[adapter_name](source, **options)
                # Progress on stderr: the JSON report only prints at the end, so a timeout would show nothing.
                # Skipped for --dry-run, whose stderr carries the report itself.
                progress = sys.stderr if not args.dry_run else None
                if progress:
                    print("START " + source["national_source_id"], file=progress, flush=True)
                try:
                    result = load(store, source, adapter, runner=runner,
                                  capacity_limit_mb=registry["capacity_limit_mb"], dry_run=args.dry_run)
                    report["runs"].append(result)
                    if progress:
                        print("DONE", source["national_source_id"], *(f"{key}={result.get(key)}" for key in
                              ("status", "fetched", "changed", "rows_written", "cursor_after")), file=progress, flush=True)
                    report["exit_code"] = max(report["exit_code"], result["exit_code"])
                finally:
                    adapter.close()
            if not report["runs"]:
                report["message"] = "無符合 runner 的已註冊 adapter"
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
