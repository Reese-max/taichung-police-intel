#!/usr/bin/env python3
"""Optional offline research probe using the official Python MCP SDK 1.30.0.

Archive the selected committed source into an owned temporary directory; only
synthetic publication copies there are modified. No production dependency is
added. Run with networking disabled as documented in the companion receipt.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

SDK_VERSION = "1.30.0"
SDK_READ_TIMEOUT_SECONDS = 15
ARTIFACTS = {"feed": "intelligence-feed.json", "status": "source-status.json", "brief": "v2-daily-brief.json"}
MARKER = "SYNTHETIC_PRIVATE_MARKER_NO_REAL_DATA"
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_INPUTS = tuple("apps/web/public/data/" + name for name in ARTIFACTS.values()) + (
    "apps/web/public/data/source-policy.json", "docs/govintel/source-policy.approved.json",
    "docs/govintel/source-catalog.v2.json", "docs/govintel/retention-rights-policy.v1.json",
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_input_hashes(source: Path) -> dict[str, str]:
    return {name: sha256(source / name) for name in CANONICAL_INPUTS}


def prepare_fictional_fixture(source: Path, destination: Path) -> dict:
    """Use the actual compiler in a separate, explicitly fictional review root."""
    spec = importlib.util.spec_from_file_location("mcp_probe_governed_fixture", source / "tests/governed_policy_fixture.py")
    if spec is None or spec.loader is None:
        raise ValueError("selected source has no governed fixture factory")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    before = canonical_input_hashes(source)
    fixture = helper.make_governed_policy_fixture(
        destination, source_root=source, rights_reviewed=True, brief_reviewed=True,
    )
    if canonical_input_hashes(source) != before:
        raise ValueError("fictional review changed canonical source inputs")
    policy = fixture["policy"]
    return {"root": fixture["root"], "scope": "FICTIONAL_OFFLINE_ONLY",
            "review": "Separate fictional metadata and derived/domain summary permission; no real approval",
            "policy_schema_version": policy["schema_version"], "policy_binding": fixture["binding"],
            "active_source_ids": policy["active_source_ids"],
            "source_origins": {row["source_id"]: row["approved_origins"] for row in policy["active_sources"]},
            "policy_sha256": sha256(fixture["paths"]["approved"]),
            "rights_matrix_sha256": sha256(fixture["paths"]["rights_matrix"])}


async def canonical_negative(source: Path) -> dict:
    """Observe current refusal without changing source policy or timestamps."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-B", str(source / "scripts/query-gateway-stdio.py")], cwd=str(source))
    async with stdio_client(params) as streams:
        async with ClientSession(*streams, read_timeout_seconds=dt.timedelta(seconds=SDK_READ_TIMEOUT_SECONDS)) as session:
            initialized = await session.initialize()
            search = await session.call_tool("search_evidence", {"limit": 1})
            assert not search.isError
            current = search.structuredContent
            assert current["result_count"] == 0
            assert current["query_coverage"]["formal_admission"]["status"] == "UNKNOWN"
            assert current["query_coverage"]["status"] == "UNKNOWN"
            assert not current["query_coverage"]["can_state_bounded_no_match"]
            errors = {}
            for tool, arguments in [("get_current_brief", {}), ("validate_answer", {"claims": [{"claim_type": "OTHER", "text": "inert research candidate"}]})]:
                result = await session.call_tool(tool, arguments)
                assert result.isError
                error = json.loads(result.content[0].text)["error"]
                assert error["code"] == "RIGHTS_BLOCKED"
                errors[tool] = error
            return {"case": "unchanged_current_source_rights", "protocol_version": initialized.protocolVersion,
                    "formal_item_count": 0, "query_coverage": current["query_coverage"],
                    "policy": current["policy"], "publication_hash": current["publication_hash"],
                    "query_generation_id": current["query_generation_id"], "rights_blocked_tools": errors,
                    "clock_override": False, "canonical_inputs_modified": False}


async def probe(source: Path, mode: str) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    data = source / "apps/web/public/data"
    docs = {key: json.loads((data / name).read_bytes()) for key, name in ARTIFACTS.items()}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    run = "CR-SYNTHETIC-ISSUE15-OFFICIAL-SDK"
    docs["feed"]["collection_run_id"] = run
    docs["status"]["latest_collection_run"].update(collection_run_id=run, status="SUCCEEDED")
    docs["brief"].update(source_collection_run_id=run, source_status_generated_at=now,
                         snapshot_complete=True, publication_status="READY")
    for value in docs.values():
        value["generated_at"] = now
    for row in docs["status"]["sources"]:
        row.update(source_health="PASS", window_completeness="COMPLETE_WITH_ITEMS", result="NEW_ITEMS",
                   freshness_status="FRESH", data_as_of=now, last_checked_at=now, last_success_at=now,
                   intelligence_gaps=[])
    for row in docs["feed"]["items"]:
        row.update(freshness_status="FRESH", source_health="PASS", data_as_of=now)
    base = copy.deepcopy(docs)
    base["brief"].setdefault("source_health", {"status": "PASS", "pass_count": len(base["status"]["sources"]),
        "stale_count": 0, "failed_count": 0, "gap_count": 0})
    rows: list[dict] = []
    previous: dict = {}

    def write(value: dict) -> None:
        for key, name in ARTIFACTS.items():
            (data / name).write_text(json.dumps(value[key], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def hashes() -> dict:
        return {key: sha256(data / name) for key, name in ARTIFACTS.items()}

    def payload(result):
        if result.isError:
            return {"isError": True, "error": json.loads(result.content[0].text)["error"]}
        return result.structuredContent

    async def case(name, body) -> None:
        params = StdioServerParameters(command=sys.executable,
                                       args=["-X", "utf8", str(source / "scripts/query-gateway-stdio.py")],
                                       cwd=str(source))
        async with stdio_client(params) as streams:
            async with ClientSession(*streams, read_timeout_seconds=dt.timedelta(seconds=SDK_READ_TIMEOUT_SECONDS)) as session:
                initialized = await session.initialize()
                result = await body(session)
                rows.append({"case": name, "protocol_version": initialized.protocolVersion,
                             "artifact_hashes": hashes(), "result": result})

    if mode == "journeys":
        write(base)

        async def journey(session):
            tools = (await session.list_tools()).tools
            names = [tool.name for tool in tools]
            assert set(names) == {"search_evidence", "get_current_brief", "get_publication_receipt",
                                  "get_source_health", "validate_answer"}
            assert all(tool.annotations.readOnlyHint and not tool.annotations.destructiveHint for tool in tools)
            brief = payload(await session.call_tool("get_current_brief", {}))
            search = payload(await session.call_tool("search_evidence", {"limit": 1}))
            repeated = payload(await session.call_tool("search_evidence", {"limit": 1}))
            health = payload(await session.call_tool("get_source_health", {}))
            receipt = payload(await session.call_tool("get_publication_receipt", {}))
            assert brief["schema_version"] == receipt["schema_version"] == 1
            assert receipt["publication_receipt"]["schema_version"] == 1
            assert search["results"] == repeated["results"]
            assert search["has_more"] and search["truncated"] and search["next_cursor"]
            item = search["results"][0]
            assert item["official_url"].startswith("https://")
            exact = payload(await session.call_tool("search_evidence", {"canonical_id": item["canonical_id"]}))
            assert [row["canonical_id"] for row in exact["results"]] == [item["canonical_id"]]
            page = payload(await session.call_tool("search_evidence", {"limit": 1, "cursor": search["next_cursor"]}))
            assert page["results"][0]["canonical_id"] != item["canonical_id"]
            assert receipt["publication_hash"] == brief["publication_hash"] == search["publication_hash"] == health["publication_hash"]
            assert receipt["publication_receipt"]["artifact_hashes"] == hashes()
            assert receipt["publication_receipt"]["current_as_of_server_clock"]
            for arguments in [{"url": "https://example.invalid"}, {"path": "/etc/passwd"},
                              {"sql": "SELECT * FROM secret"}, {"limit": 101}]:
                assert payload(await session.call_tool("search_evidence", arguments))["isError"]
            previous.update(generation=receipt["query_generation_id"], publication_hash=receipt["publication_hash"],
                            cursor=search["next_cursor"])
            return {"tool_names": names, "publication_hash": receipt["publication_hash"],
                    "generation": receipt["query_generation_id"], "freshness": receipt["freshness"],
                    "current": True, "deterministic_projection": True, "exact_official_locator": item["official_url"],
                    "canonical_ref": item["canonical_ref"], "pagination_truncation": True,
                    "malicious_argument_and_overlimit_rejections": 4}

        await case("sdk_full_brief_search_evidence_health_receipt_journey", journey)
        mutations = {
            "FAILED": {"source_health": "FAILED", "result": "FAILED", "window_completeness": "PARTIAL"},
            "PARTIAL": {"source_health": "DEGRADED", "result": "PARTIAL", "window_completeness": "PARTIAL"},
            "CONFLICT": {"source_health": "QUARANTINED", "result": "PARTIAL", "window_completeness": "PARTIAL",
                         "intelligence_gaps": ["CONFLICT"]},
            "STALE": {"source_health": "PASS", "result": "NEW_ITEMS", "freshness_status": "STALE"},
        }
        for state, mutation in mutations.items():
            fixture = copy.deepcopy(base)
            fixture["status"]["sources"][0].update(mutation)
            write(fixture)
            source_id = fixture["status"]["sources"][0]["source_id"]

            async def failure(session, expected=state, selected=source_id):
                health = payload(await session.call_tool("get_source_health", {"source_id": selected}))
                empty = payload(await session.call_tool("search_evidence", {"source_id": selected, "q": "NEVER_MATCH_SDK_SYNTHETIC"}))
                assert empty["result_count"] == 0 and not empty["answerable_no_match"] and empty["source_gaps"]
                assert not health["query_coverage"]["can_state_bounded_no_match"]
                visible = health["sources"][0]
                assert expected in json.dumps(visible)
                return {"source_state": expected, "client_source": visible, "freshness": empty["freshness"],
                        "client_source_gaps": empty["source_gaps"], "answerable_no_match": False,
                        "can_state_bounded_no_match": False}

            await case("sdk_client_visible_" + state, failure)
        write(base)

        async def missing(session):
            result = payload(await session.call_tool("search_evidence", {"source_id": "S-NOT-IN-APPROVED-SNAPSHOT", "q": "NEVER_MATCH"}))
            assert result["source_gaps"] and result["source_gaps"][0]["reason"] == "NOT_IN_APPROVED_SNAPSHOT"
            assert not result["answerable_no_match"]
            return {"freshness": result["freshness"], "source_gaps": result["source_gaps"],
                    "verification_summary": result["verification_summary"], "answerable_no_match": False}

        await case("sdk_client_source_not_available", missing)
        moved = copy.deepcopy(base)
        moved["brief"]["status_message"] = "Synthetic local Issue15 head-B fixture; no real publication claim"
        write(moved)

        async def head(session):
            receipt = payload(await session.call_tool("get_publication_receipt", {}))
            assert receipt["publication_hash"] != previous["publication_hash"]
            assert receipt["query_generation_id"] != previous["generation"]
            stale_generation = payload(await session.call_tool("search_evidence", {"expected_generation": previous["generation"]}))
            stale_cursor = payload(await session.call_tool("search_evidence", {"limit": 1, "cursor": previous["cursor"]}))
            assert stale_generation["isError"] and stale_cursor["isError"]
            return {"old_publication_hash": previous["publication_hash"], "new_publication_hash": receipt["publication_hash"],
                    "old_generation": previous["generation"], "new_generation": receipt["query_generation_id"],
                    "old_expected_generation_rejected": stale_generation, "old_cursor_rejected": stale_cursor,
                    "scope": "Fresh server restart; no hot-reload or deployed-host claim"}

        await case("sdk_publication_head_switch", head)
    else:
        if mode == "private-green":
            write(base)
            async def valid_control(session):
                for tool in ("get_current_brief", "get_source_health"):
                    assert not (await session.call_tool(tool, {})).isError
                return {"valid_reviewed_fixture_control": True}
            await case("sdk_valid_reviewed_private_control", valid_control)
        for name, tool in [("brief_nested_private_field", "get_current_brief"),
                           ("source_gap_nested_private_field", "get_source_health")]:
            fixture = copy.deepcopy(base)
            if tool == "get_current_brief":
                fixture["brief"]["source_health"]["private_operational_note"] = MARKER
            else:
                fixture["status"]["sources"][0]["intelligence_gaps"] = [{"private_case": MARKER}]
            write(fixture)

            async def private(session, selected=tool):
                result = await session.call_tool(selected, {})
                returned = MARKER in json.dumps(result.model_dump(mode="json"))
                error = payload(result).get("error") if result.isError else None
                if mode == "private-red":
                    assert not result.isError and returned
                else:
                    assert result.isError and not returned and error["code"] == "PUBLIC_PROJECTION_INVALID"
                return {"isError": result.isError, "error": error, "private_marker_returned": returned,
                        "fixture": "Synthetic canary only; no real private data"}

            await case(name, private)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", default="HEAD", help="Committed source to archive and probe")
    parser.add_argument("--mode", choices=("current-negative", "journeys", "private-green", "private-red"), default="current-negative")
    parser.add_argument("--fixture-policy", choices=("fictional-reviewed",),
                        help="Explicit fictional metadata and summary permissions in a separate temporary root")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT.resolve()):
        parser.error("write research receipts outside the source checkout")
    if args.mode in {"journeys", "private-green"} and args.fixture_policy != "fictional-reviewed":
        parser.error("positive research requires --fixture-policy fictional-reviewed")
    if args.mode in {"current-negative", "private-red"} and args.fixture_policy:
        parser.error("the unmodified current/historical control cannot activate a fictional review")
    if importlib.metadata.version("mcp") != SDK_VERSION:
        parser.error("use an isolated diagnostic environment with mcp==" + SDK_VERSION)
    import anyio

    commit = git("rev-parse", "--verify", args.source_ref + "^{commit}").decode().strip()
    tree = git("rev-parse", commit + "^{tree}").decode().strip()
    archive = git("archive", "--format=tar", commit)
    with tempfile.TemporaryDirectory(prefix="issue15-offline-mcp-") as owned:
        source = Path(owned) / "canonical"
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(source, filter="data")
        runtime_files = ["scripts/query-gateway.py", "scripts/query-gateway-stdio.py",
                         "scripts/query-store.py", "scripts/source-policy.py"]
        for name in ("intel_v2/public_brief.py", "scripts/source-governance.py",
                     "scripts/retention-policy.py", "intel_v2/freshness_policy.py",
                     "tests/governed_policy_fixture.py"):
            if (source / name).exists():
                runtime_files.append(name)  # Some paths are absent in the historical red source.
        runtime_hashes = {name: sha256(source / name) for name in runtime_files}
        frozen_hashes = {str(path.relative_to(source)): sha256(path)
                         for path in sorted((source / "docs/research").glob("issue-15-*.json"))}
        original_hashes = canonical_input_hashes(source)
        current_negative = None if args.mode == "private-red" else anyio.run(canonical_negative, source)
        fictional = None
        if args.fixture_policy:
            fictional = prepare_fictional_fixture(source, Path(owned) / "fictional-reviewed")
        measured = fictional["root"] if fictional else source
        rows = [] if args.mode == "current-negative" else anyio.run(probe, measured, args.mode)
        # The historical red mode intentionally edits its disposable archive's
        # old publication copies; current-source modes leave the archive intact.
        if args.mode != "private-red":
            assert canonical_input_hashes(source) == original_hashes
        assert {name: sha256(source / name) for name in frozen_hashes} == frozen_hashes
        if fictional:
            fictional = {key: value for key, value in fictional.items() if key != "root"}
    receipt = {"schema_version": 2, "repository": "Reese-max/taichung-police-intel", "issue": 15,
               "mode": args.mode, "source_commit": commit, "source_tree": tree,
               "runtime_file_sha256": runtime_hashes, "client": "Official Python MCP SDK", "client_version": SDK_VERSION,
               "transport": "STDIO", "observed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "client_read_timeout_seconds": SDK_READ_TIMEOUT_SECONDS,
               "fixture_scope": ("Disposable historical publication copies intentionally mutated; no current approval" if args.mode == "private-red" else
                   "Canonical archive unchanged; positive permissions exist only in a separate explicitly fictional root"),
               "canonical_source_input_sha256": original_hashes,
               "frozen_research_receipt_sha256": frozen_hashes,
               "canonical_current_negative": current_negative, "fictional_governance": fictional,
               "network_requirement": "Run inside a network-disabled sandbox; probe performs no provider calls",
               "cases": rows, "limitations": ["Default five read-only metadata tools only",
                   "Head change is exercised by fresh-server restart, not live hot reload",
                   "Fictional timestamps and permissions do not verify actual freshness or rights",
                   "No real-source promotion, production host, OAuth or human usability claim"],
               "result": "CONFIRMED_RED_MAIN_NESTED_FIELD_LEAK" if args.mode == "private-red" else "PASS"}
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": receipt["result"], "cases": len(rows), "source_commit": commit, "client_version": SDK_VERSION}))


if __name__ == "__main__":
    main()
